"""UniversalToolInvocation — the fabric's one invocation shape.

WHY frozen: an invocation crosses adapter boundaries and lands in trajectories;
it must not be mutable in flight. WHY the ATLAS context fields: correlation_id
is the system-wide join key (audit, events, logs) and task_id keeps observability
attached to the originating task (Part 1 §28) — the capability dispatcher takes
it directly and the native dispatcher gets it via the correlation ID.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from atlas.infra.ids import CorrelationId


class InvocationSource(StrEnum):
    """Which surface requested the execution."""

    REASONING = "reasoning"
    API = "api"
    CLI = "cli"
    AUTOMATION = "automation"
    SYSTEM = "system"
    TEST = "test"


class UniversalToolInvocation(BaseModel):
    """Everything an adapter needs to execute one tool operation."""

    model_config = ConfigDict(frozen=True)

    tool_id: str
    operation: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    correlation_id: CorrelationId
    task_id: str | None = None
    source: InvocationSource = InvocationSource.SYSTEM
    timeout_s: float | None = Field(default=None, gt=0)
    idempotency_key: str | None = None

    @field_validator("tool_id")
    @classmethod
    def _tool_id_must_be_canonical(cls, value: str) -> str:
        from atlas.tooling.models.identity import parse_tool_id

        parse_tool_id(value)  # raises ValueError on malformed identity
        return value

    @field_validator("operation")
    @classmethod
    def _operation_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("operation must be a non-empty string")
        return value
