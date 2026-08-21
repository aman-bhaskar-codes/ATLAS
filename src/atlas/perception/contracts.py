"""Universal perception contracts (Phases 1-3).

ONE protocol, MANY substrates. The reasoning core receives a
PerceptionSnapshot and never knows whether it came from a browser DOM, a macOS
AX tree, an Android UI hierarchy, or an API response. Substrate adapters
produce snapshots; the core consumes them.

WHY snapshots are structured-first: screenshots alone are lossy, expensive, and
unsafe to feed to cloud models. Every snapshot carries structured elements when
the substrate can provide them; visual data is a fallback/confirmation channel
(Phase 3 policy).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from atlas.perception.types import ScreenState


class Substrate(StrEnum):
    """The physical/logical surface being perceived or controlled."""

    BROWSER = "browser"
    MACOS = "macos"
    ANDROID = "android"
    FILESYSTEM = "filesystem"
    SHELL = "shell"
    API = "api"


class PerceptionModality(StrEnum):
    """Kinds of evidence a substrate can supply (Phase 2)."""

    VISION = "vision"  # screenshot / pixels
    TEXT = "text"  # extracted text content
    STRUCTURE = "structure"  # generic structure (path tree, schema)
    ACCESSIBILITY = "accessibility"  # AX tree / UI hierarchy
    DOM = "dom"  # browser DOM
    WINDOW_TREE = "window_tree"  # OS window hierarchy
    PROCESS_STATE = "process_state"
    APP_STATE = "app_state"  # package/activity, auth state, etc.
    API_STATE = "api_state"  # schema/response/status


class HealthStatus(BaseModel):
    """Adapter health — surfaced to EnvironmentDetector and WorldState."""

    model_config = {"frozen": True}
    available: bool
    detail: str = ""
    permission_missing: str | None = None  # e.g. "macos_accessibility"


class VisualEvidence(BaseModel):
    """Screenshot reference carried by a snapshot.

    WHY bytes are optional: most reasoning happens over structured elements;
    pixels are attached only when visual grounding or verification needs them
    (Phase 21 screenshot strategy). `redacted` marks that sensitive regions
    were masked before any model could see them (Phase 17).
    """

    model_config = {"frozen": True}
    format: str = "png"
    data: bytes | None = None
    width: int | None = None
    height: int | None = None
    redacted: bool = False


class PerceivedElement(BaseModel):
    """One interactable element, normalized across substrates.

    Unifies perception.UIElement (macOS), browser ElementRef/AccessibilityNode,
    and Android UI-hierarchy nodes into one shape the planner can reason over.
    """

    model_config = {"frozen": True}
    role: str  # normalized role: button / text_field / link / ...
    label: str | None = None  # accessible name / visible text
    value: str | None = None  # current value if any
    enabled: bool = True
    focused: bool = False
    sensitive: bool = False  # password/OTP/credential field (Phase 17)
    stable_id: str | None = None  # strongest substrate-native identity
    bounds: tuple[int, int, int, int] | None = None  # x,y,w,h
    confidence: float = 1.0  # structural evidence defaults to high confidence
    metadata: dict[str, str] = Field(default_factory=dict)


class PerceptionSnapshot(BaseModel):
    """Unified observation of any substrate at one moment (Phase 1).

    Checkpointable and serializable: this is what enters WorldState, feeds the
    planner, and is re-captured after actions for verification.
    """

    model_config = {"frozen": True}
    id: str
    substrate: Substrate
    source: str  # adapter/backend name for legibility + cost accounting
    captured_ts: datetime
    # Surface identity (substrate-dependent, all optional)
    url: str | None = None  # browser
    app_name: str | None = None  # macOS / Android package
    window_title: str | None = None
    activity: str | None = None  # Android current activity
    # Evidence channels
    modalities: tuple[PerceptionModality, ...] = ()
    elements: tuple[PerceivedElement, ...] = ()
    text: str | None = None
    visual: VisualEvidence | None = None
    state: dict[str, Any] = Field(default_factory=dict)  # substrate state (loading, auth, scroll)
    metadata: dict[str, str] = Field(default_factory=dict)
    confidence: float = 1.0  # overall snapshot reliability
    sensitive: bool = False  # surface-level sensitivity (banking app, etc.)
    note: str | None = None  # degradation/fallback explanation

    def interactable(self) -> tuple[PerceivedElement, ...]:
        """Elements a planner can act on (drops pure static structure)."""
        return tuple(e for e in self.elements if e.role not in ("group", "window", "other"))

    def summarize(self, limit: int = 40) -> str:
        """Compact text rendering for model context (bounded tokens)."""
        head = f"substrate={self.substrate.value} app={self.app_name or self.url or '-'} source={self.source}"
        if self.sensitive:
            head += " [SENSITIVE]"
        lines = [head]
        for el in self.elements[:limit]:
            bit = f"  {el.role}"
            if el.label:
                bit += f" {el.label!r}"
            if el.value:
                bit += f" = {el.value!r}"
            if el.sensitive:
                bit += " [SENSITIVE-FIELD]"
            if not el.enabled:
                bit += " (disabled)"
            if el.focused:
                bit += " (focused)"
            lines.append(bit)
        if len(self.elements) > limit:
            lines.append(f"  ... +{len(self.elements) - limit} more elements")
        return "\n".join(lines)


@runtime_checkable
class PerceptionAdapter(Protocol):
    """THE universal perception contract (Phase 1).

    Every substrate implements exactly this. The core depends on the protocol,
    never on Playwright/PyObjC/ADB specifics.
    """

    substrate: Substrate

    async def snapshot(self, target: Any | None = None) -> PerceptionSnapshot: ...

    async def health(self) -> HealthStatus: ...

    async def capabilities(self) -> tuple[PerceptionModality, ...]: ...


def snapshot_from_screen_state(state: ScreenState, *, snapshot_id: str, captured_ts: datetime) -> PerceptionSnapshot:
    """Convert the macOS-native ScreenState into the universal snapshot.

    WHY a converter instead of a rewrite: ScreenState/UIElement are the proven
    macOS representation; the universal contract composes on top.
    """
    elements = tuple(
        PerceivedElement(
            role=el.role,
            label=el.label,
            value=el.value,
            enabled=el.enabled,
            focused=el.focused,
            stable_id=el.ax_path,
            bounds=el.bounds,
            confidence=0.95,  # AX tree is structured but labels can be missing
        )
        for el in state.elements
    )
    return PerceptionSnapshot(
        id=snapshot_id,
        substrate=Substrate.MACOS,
        source=state.source.value,
        captured_ts=captured_ts,
        app_name=state.app_name,
        window_title=state.window_title,
        modalities=(PerceptionModality.ACCESSIBILITY,),
        elements=elements,
        sensitive=state.sensitive,
        confidence=0.9 if state.elements else 0.3,
        note=state.note,
    )
