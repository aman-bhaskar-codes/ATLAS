"""Shared cross-boundary contracts.

WHY centralized: these types are imported by both L0 and L1. Keeping them in
one leaf module (no atlas imports of its own beyond ids) avoids cycles and
gives every layer a single source of truth for wire shapes.
"""

from __future__ import annotations

from datetime import datetime
from enum import IntEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from atlas.infra.ids import CorrelationId

Decision = Literal["allow", "deny", "require_confirm"]
Source = Literal["cli", "file", "whatsapp", "api", "scheduler", "system"]


class Tier(IntEnum):
    AUTO = 0
    NOTIFY = 1
    CONFIRM = 2
    BLOCK = 3


class _Frozen(BaseModel):
    """Base for immutable value objects. WHY frozen: contracts crossing
    boundaries must not be mutated in place by a consumer."""

    model_config = ConfigDict(frozen=True)


class ToolRequest(_Frozen):
    correlation_id: CorrelationId
    tool: str
    operation: str
    args: dict[str, Any] = Field(default_factory=dict)
    declared_tier_hint: Tier | None = None


class SideEffect(_Frozen):
    kind: str
    target: str
    detail: str | None = None
    reversible: bool = False


class ToolResult(_Frozen):
    ok: bool
    output: Any = None
    side_effects: tuple[SideEffect, ...] = ()
    error: str | None = None
    duration_ms: int | None = None


class SafetyDecision(_Frozen):
    decision: Decision
    tier: Tier
    reason: str
    requires_sandbox: bool = True
    matched_rule: str | None = None


class ModelTarget(IntEnum):
    LOCAL_FAST = 0
    LOCAL_HEAVY = 1
    CLOUD = 2


class ModelRequest(_Frozen):
    correlation_id: CorrelationId
    prompt: str
    system: str | None = None
    force_target: ModelTarget | None = None
    needs_deep_reasoning: bool = False
    stakes_tier: Tier = Tier.AUTO
    thinking: bool | None = None
    max_tokens: int = 1024
    temperature: float = 0.2


class TokenCost(_Frozen):
    input_tokens: int = 0
    output_tokens: int = 0
    usd: float = 0.0


class ModelResponse(_Frozen):
    text: str
    target: ModelTarget
    model: str
    cost: TokenCost = TokenCost()
    latency_ms: int = 0
    truncated: bool = False


class AuditRecord(_Frozen):
    correlation_id: CorrelationId
    ts: datetime
    actor: str
    action: str
    tool: str | None = None
    tier: Tier | None = None
    decision: Decision | None = None
    outcome: str | None = None
    payload: dict[str, Any] | None = None
    cost_tokens: int = 0
    cost_usd: float = 0.0


class InboundEvent(BaseModel):
    """An inbound request from any transport (CLI, API, scheduler)."""
    model_config = ConfigDict(frozen=True)
    correlation_id: CorrelationId
    source: Source
    content: str
