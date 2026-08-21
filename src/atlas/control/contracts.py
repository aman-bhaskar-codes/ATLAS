"""Universal control contract (Phases 4, 6).

ONE dispatch protocol for every substrate. The reasoning core emits a
ControlAction (substrate-independent); adapters translate it to Playwright /
AppleScript / ADB. The core MUST NOT contain `if mac:` / `if android:`
branches — those live inside adapters.

EVERY action carries confidence, evidence, risk and reversibility (Phase 6):
the engine refuses to execute weak guesses and the Safety Engine sees the full
picture before authorization.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from atlas.infra.types import Tier
from atlas.perception.contracts import HealthStatus, Substrate
from atlas.perception.targets import TargetRef


class ActionCapability(StrEnum):
    """Logical capability an action belongs to (Phase 30 vocabulary)."""

    UI = "ui"  # click / type / press / scroll / select / drag
    APP = "app"  # launch / close / switch_window
    DEVICE = "device"  # back / home / wake (mobile)
    NAVIGATION = "navigation"  # navigate / back / forward / reload
    OBSERVATION = "observation"  # observe / screenshot / extract


class ControlAction(BaseModel):
    """Substrate-independent action proposal.

    NOTE: this is a PROPOSAL. It becomes an execution only after the engine
    validates the target and the SafetyEngine authorizes it (Phase 15/48).
    """

    model_config = {"frozen": True}
    capability: ActionCapability
    operation: str  # click / type / press / navigate / launch / extract / ...
    target: TargetRef | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    # Phase 6 — every computer-use action declares these:
    confidence: float = 1.0  # 0..1 target-resolution confidence
    evidence: str = ""  # how the target was resolved (accessibility/DOM/...)
    risk_hint: Tier | None = None  # proposer's tier guess; classifier is authoritative
    reversible: bool = True


class ControlResult(BaseModel):
    """Adapter execution outcome.

    `ok=True` means the adapter performed the primitive — it does NOT mean the
    goal was achieved. Verification (Phase 14) re-perceives to confirm the
    expected state change. Phase 47: never claim success without evidence.
    """

    model_config = {"frozen": True}
    ok: bool
    output: Any = None
    error: str | None = None
    evidence: str = ""  # what the adapter observed post-execution
    duration_ms: float | None = None


@runtime_checkable
class ControlAdapter(Protocol):
    """THE universal control contract (Phase 4)."""

    substrate: Substrate

    async def dispatch(self, action: ControlAction) -> ControlResult: ...

    async def health(self) -> HealthStatus: ...

    async def capabilities(self) -> tuple[str, ...]:
        """Operations this adapter supports (click/type/launch/...)."""
        ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...
