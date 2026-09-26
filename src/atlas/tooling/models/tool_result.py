"""UniversalToolResult — one normalized result shape for every adapter.

WHY structured failures instead of strings (Part 1 §10/§83 direction): callers
and future routing must branch on failure KIND, and retry logic needs
``retryable`` — none of that survives stringly errors. The underlying message
is preserved verbatim so no information is lost.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from atlas.infra.types import SideEffect
from atlas.tooling.models.tool_provenance import ToolProvenance


class FailureKind(StrEnum):
    """Machine-readable failure taxonomy for execution-domain outcomes."""

    EXECUTION_ERROR = "execution_error"
    POLICY_DENIED = "policy_denied"
    HALTED = "halted"
    UNAVAILABLE = "unavailable"
    VALIDATION = "validation"
    TIMEOUT = "timeout"


class ToolFailure(BaseModel):
    """A structured, typed execution failure."""

    model_config = ConfigDict(frozen=True)

    kind: FailureKind
    message: str
    retryable: bool = False

    @property
    def code(self) -> str:
        """Stable machine code, aligned with the tooling error taxonomy."""
        return f"tooling.{self.kind.value}"


class UniversalToolResult(BaseModel):
    """The normalized outcome of one universal tool invocation."""

    model_config = ConfigDict(frozen=True)

    ok: bool
    tool_id: str
    provider: str = ""
    data: Any = None
    error: ToolFailure | None = None
    duration_ms: int | None = None
    side_effects: tuple[SideEffect, ...] = ()
    provenance: tuple[ToolProvenance, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def failure(
        cls,
        *,
        tool_id: str,
        kind: FailureKind,
        message: str,
        retryable: bool = False,
        provider: str = "",
        duration_ms: int | None = None,
    ) -> UniversalToolResult:
        """Build a normalized failure result (ok=False, structured error)."""
        return cls(
            ok=False,
            tool_id=tool_id,
            provider=provider,
            error=ToolFailure(kind=kind, message=message, retryable=retryable),
            duration_ms=duration_ms,
        )
