"""Universal target model (Phase 5).

WHY one TargetRef for every substrate: the reasoning core must address "the
Settings button" identically whether it lives in a DOM, a macOS AX tree, or an
Android UI hierarchy. Substrate-specific addressing (css selector, resource-id,
ax_path) rides along as a strategy kind — adapters translate, the core never
branches on substrate.

Resolution follows a RELIABILITY ORDER: stable identifiers first, coordinates
last. Never default to coordinates when a stable target exists.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class TargetStrategy(StrEnum):
    """How a target is addressed. Ordered by reliability in RESOLUTION_ORDER."""

    STABLE_ID = "stable_id"  # provider-guaranteed stable identifier
    ACCESSIBILITY_ID = "accessibility_id"  # AX identifier / ax_path
    ROLE = "role"  # semantic role (button, text_field, ...)
    TEXT = "text"  # visible text / accessible name
    SEMANTIC = "semantic"  # natural-language description
    DOM_SELECTOR = "dom_selector"  # CSS selector (browser)
    XPATH = "xpath"  # DOM or UI-hierarchy xpath
    RESOURCE_ID = "resource_id"  # Android resource-id
    WINDOW_ID = "window_id"  # window/tab handle
    IMAGE_REF = "image_ref"  # visual template reference
    COORDINATES = "coordinates"  # last resort


# Reliability order for target resolution (Phase 5). Adapters MUST attempt
# strategies in this order and only fall forward when the stronger strategy
# cannot resolve.
RESOLUTION_ORDER: tuple[TargetStrategy, ...] = (
    TargetStrategy.STABLE_ID,
    TargetStrategy.ACCESSIBILITY_ID,
    TargetStrategy.ROLE,
    TargetStrategy.SEMANTIC,
    TargetStrategy.DOM_SELECTOR,
    TargetStrategy.RESOURCE_ID,
    TargetStrategy.XPATH,
    TargetStrategy.TEXT,
    TargetStrategy.IMAGE_REF,
    TargetStrategy.WINDOW_ID,
    TargetStrategy.COORDINATES,
)


class TargetRef(BaseModel):
    """Substrate-independent element address.

    `value` carries the strategy-specific locator (role name, text, css, xpath,
    "x,y" for coordinates). Optional fields let a caller supply corroborating
    evidence (e.g. role + text together) which raises resolution confidence.
    """

    model_config = {"frozen": True}
    strategy: TargetStrategy
    value: str
    # Corroborating hints (all optional; adapters use what they can)
    role: str | None = None
    text: str | None = None
    label: str | None = None  # accessible name (macOS AX / Android content-desc)
    semantic: str | None = None
    nth: int | None = None  # disambiguate duplicates (0-based)
    exact: bool = False  # exact text match vs contains
    x: int | None = None
    y: int | None = None


class ResolvedTarget(BaseModel):
    """Outcome of resolving a TargetRef against a PerceptionSnapshot.

    WHY evidence + confidence: computer-use actions must never execute on a
    weak guess. The engine gates execution on confidence and surfaces the
    evidence chain for audit and the debug view.
    """

    model_config = {"frozen": True}
    target: TargetRef
    strategy_used: TargetStrategy
    element_index: int | None = None  # index into snapshot.elements
    stable_handle: str | None = None  # adapter-native handle (ax_path/css/resource-id)
    bounds: tuple[int, int, int, int] | None = None  # x,y,w,h if known
    confidence: float  # 0..1
    evidence: str  # human-readable: how this was resolved
