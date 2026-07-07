"""Platform contracts.

WHY a richer InferenceRequest than Phase-1 ModelRequest: the platform routes on
capabilities + constraints, not just a prompt. We keep a compatibility shim so
the existing gateway.complete(ModelRequest) callers keep working (see gateway).
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum

from pydantic import BaseModel

from atlas.infra.ids import CorrelationId
from atlas.intelligence.capabilities import CapabilitySet


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class Message(BaseModel):
    model_config = {"frozen": True}
    role: Role
    content: str


class Usage(BaseModel):
    model_config = {"frozen": True}
    input_tokens: int = 0
    output_tokens: int = 0
    usd: float = 0.0


class ModelSpec(BaseModel):
    """Config-driven metadata for one model. Loaded from models.yaml."""
    model_config = {"frozen": True}
    id: str                       # logical id, e.g. 'glm-5.2'
    provider: str                 # provider adapter name
    provider_model: str           # provider's own model string
    context_length: int
    usd_per_1m_input: float
    usd_per_1m_output: float
    latency_estimate_ms: int = 2000
    capabilities: CapabilitySet = frozenset()
    max_concurrency: int = 4
    supports_streaming: bool = False
    supports_structured_output: bool = False
    supports_reasoning: bool = False
    supports_vision: bool = False
    supports_tool_calling: bool = False
    quality_score: float = 0.5     # 0..1 curated prior
    reliability_score: float = 1.0 # 0..1 updated by health monitor
    preferred_tasks: tuple[str, ...] = ()
    enabled: bool = True


class Constraints(BaseModel):
    """Caller/selection constraints."""
    model_config = {"frozen": True}
    max_latency_ms: int | None = None
    max_cost_usd: float | None = None
    min_context: int | None = None
    require_streaming: bool = False
    prefer_local: bool = False
    pinned_model: str | None = None   # explicit override (still budget/health checked)


class InferenceRequest(BaseModel):
    model_config = {"frozen": True}
    correlation_id: CorrelationId
    messages: Sequence[Message]
    required_capabilities: CapabilitySet = frozenset()
    constraints: Constraints = Constraints()
    max_tokens: int = 1024
    temperature: float = 0.2
    stream: bool = False
    task_id: str | None = None


class InferenceResponse(BaseModel):
    model_config = {"frozen": True}
    text: str
    model_id: str
    provider: str
    usage: Usage = Usage()
    latency_ms: int = 0
    attempts: int = 1
    fell_back: bool = False
    truncated: bool = False


class StreamChunk(BaseModel):
    model_config = {"frozen": True}
    delta: str
    done: bool = False
