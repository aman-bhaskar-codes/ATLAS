"""Tool runtime state — minimal for Part 1, extensible for Part 8.

WHY these four states only: Part 1 needs registration truth and an explicit
disabled state. Health/circuit-breaker machinery belongs to Part 8; the state
enum is a StrEnum so Part 8 can extend it (DEGRADED, QUOTA_EXHAUSTED, ...)
without breaking serialized trajectories.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class ToolRuntimeState(StrEnum):
    REGISTERED = "registered"  # registered, adapter not yet initialized/validated
    READY = "ready"  # adapter initialized and validated; executable
    DISABLED = "disabled"  # runtime-disabled; inspectable but not executable
    FAILED = "failed"  # adapter initialization or validation failed


class ToolStatus(BaseModel):
    """Runtime status of one registered tool. Mutable state lives HERE, never
    on the (immutable) definition — mutating catalog metadata during execution
    is exactly what Part 1 §42 forbids."""

    model_config = ConfigDict(frozen=True)

    state: ToolRuntimeState = ToolRuntimeState.REGISTERED
    detail: str = ""
