"""Shared cross-boundary contracts.

WHY centralized: these types are imported by both L0 and L1. Keeping them in
one leaf module (no atlas imports of its own beyond ids) avoids cycles and
gives every layer a single source of truth for wire shapes.
"""

from __future__ import annotations

from datetime import datetime
from enum import IntEnum, StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from atlas.infra.ids import CorrelationId

Decision = Literal["allow", "deny", "require_confirm"]
Source = Literal["cli", "file", "whatsapp", "api", "scheduler", "system"]


class Tier(IntEnum):
    AUTO = 0  # Vamos Tier 1: read-only, no side effects — auto-approve
    NOTIFY = 1  # Vamos Tier 2: reversible side effects — auto-approve with notification
    CONFIRM = 2  # Vamos Tier 3: irreversible/external — require explicit user approval
    DANGEROUS = 3  # Vamos Tier 4: high-impact (delete DB, spend money, access creds) — approval + confirmation code
    BLOCK = 4  # Hard-blocked: never executed


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


class ModelCapability(StrEnum):
    PLANNING = "planning"
    REASONING = "reasoning"
    CODING = "coding"
    VISION = "vision"
    TOOL_CALLING = "tool_calling"
    STRUCTURED_OUTPUT = "structured_output"
    LONG_CONTEXT = "long_context"
    EMBEDDING = "embedding"
    CLASSIFICATION = "classification"
    SUMMARIZATION = "summarization"
    TRANSLATION = "translation"
    REFLECTION = "reflection"
    CONSENSUS = "consensus"
    JSON_GENERATION = "json_generation"
    STREAMING = "streaming"


ModelCapabilitySet = frozenset[ModelCapability]


# ---------------------------------------------------------------------------
# Zero-Cost-First Policy Enums
# WHY here (not in intelligence/): these cross L0/L1 boundaries. The selector,
# runtime, governor, CLI, and API all need them — keeping them in the shared
# leaf module avoids import cycles while giving the whole stack a single
# vocabulary for cost, network, and privacy constraints.
# ---------------------------------------------------------------------------


class CostClass(StrEnum):
    """Semantic cost tier for a provider or model.

    LOCAL       — runs on user hardware, $0 always
    FREE        — cloud service, $0, no quota limit known
    FREE_QUOTA  — cloud service, $0 within a daily/monthly quota
    PAID        — costs money per token/request
    """

    LOCAL = "local"
    FREE = "free"
    FREE_QUOTA = "free_quota"
    PAID = "paid"


class CostPolicy(StrEnum):
    """User-chosen cost enforcement mode.

    ZERO_COST       — hard-block ALL paid providers, even if keys exist
    FREE_ONLY       — only local + free cloud (no paid)
    FREE_PREFERRED  — prefer free, paid only if user explicitly authorizes
    BALANCED        — optimize cost/quality/latency
    UNRESTRICTED    — use whatever is configured
    """

    ZERO_COST = "zero_cost"
    FREE_ONLY = "free_only"
    FREE_PREFERRED = "free_preferred"
    BALANCED = "balanced"
    UNRESTRICTED = "unrestricted"


class NetworkPolicy(StrEnum):
    """Network access constraint, enforced before provider selection.

    OFFLINE       — no network at all, only local tools/models
    LOCAL_ONLY    — no external API calls (LAN services like Ollama OK)
    FREE_CLOUD    — only approved free-tier cloud providers
    UNRESTRICTED  — any configured provider
    """

    OFFLINE = "offline"
    LOCAL_ONLY = "local_only"
    FREE_CLOUD = "free_cloud"
    UNRESTRICTED = "unrestricted"


class PrivacyClass(StrEnum):
    """Data sensitivity classification — drives provider routing.

    PUBLIC     — can go anywhere (free cloud, paid, etc.)
    INTERNAL   — internal data, local preferred but cloud OK
    PRIVATE    — personal data, only local or explicitly approved providers
    SENSITIVE  — PII/financial, local strongly preferred
    SECRET     — must stay local, cloud routing hard-blocked
    """

    PUBLIC = "public"
    INTERNAL = "internal"
    PRIVATE = "private"
    SENSITIVE = "sensitive"
    SECRET = "secret"


class ModelTarget(IntEnum):
    LOCAL_FAST = 0
    LOCAL_HEAVY = 1
    CLOUD = 2


class ToolCallSpec(_Frozen):
    """A tool advertised to the model (provider-native function calling).

    `parameters` is a JSON-schema object; providers serialize it into their own
    wire format and ATLAS normalizes the model's choices back into
    ProviderToolCall so orchestration never sees vendor schemas.
    """

    name: str
    description: str = ""
    parameters: dict[str, object] = Field(default_factory=dict)


class ProviderToolCall(_Frozen):
    """A tool invocation chosen by the model, in ATLAS-normalized form."""

    id: str = ""
    name: str
    arguments: dict[str, object] = Field(default_factory=dict)


class ModelRequest(_Frozen):
    correlation_id: CorrelationId
    prompt: str
    required_capabilities: ModelCapabilitySet = frozenset()
    system: str | None = None
    force_target: ModelTarget | None = None
    needs_deep_reasoning: bool = False
    stakes_tier: Tier = Tier.AUTO
    thinking: bool | None = None
    max_tokens: int = 1024
    temperature: float = 0.2
    tools: tuple[ToolCallSpec, ...] = ()  # empty = no native function calling


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
    tool_calls: tuple[ProviderToolCall, ...] = ()  # non-empty = model chose tools
    reasoning_details: str | None = None


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
