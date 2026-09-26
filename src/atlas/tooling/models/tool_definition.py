"""UniversalToolDefinition — the canonical, immutable tool descriptor.

One representation for every executable capability regardless of how it
executes (native Python, capability platform, future MCP/HTTP/...). This is the
model the catalog (Part 2), router (Part 3), tool-RAG (Part 6), API/CLI, and
trajectories all speak.

WHY frozen (Part 1 §42): definitions are catalog metadata; runtime state lives
in ``ToolStatus`` on the registry entry. Mutation during execution would make
routes unreplayable and audit ambiguous.

WHY nested ``policy``: the policy descriptor (§12) is one coherent structure
consumed by routing; keeping it nested means routing filters read one field and
there is exactly one source for each policy fact.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from atlas.infra.types import CostClass, Tier
from atlas.tooling.models.identity import ToolNamespace, build_tool_id, parse_tool_id
from atlas.tooling.models.tool_policy import ToolPolicyMetadata
from atlas.tooling.models.tool_provenance import ToolProvenance
from atlas.tooling.models.tool_schema import ToolInputSchema


class ExecutionType(StrEnum):
    """HOW the tool executes. Part 1 uses NATIVE and CAPABILITY; the remaining
    members keep future transports (MCP, HTTP, ...) representable without
    touching current code (Part 1 §34)."""

    NATIVE = "native"
    CAPABILITY = "capability"
    MCP = "mcp"
    HTTP = "http"
    GRAPHQL = "graphql"
    CLI = "cli"
    BROWSER = "browser"
    COMPUTER = "computer"
    LOCAL = "local"
    REMOTE = "remote"


class Locality(StrEnum):
    """Where execution happens — a routing signal (offline mode must be able
    to eliminate REMOTE tools before ranking)."""

    LOCAL = "local"
    REMOTE = "remote"
    HYBRID = "hybrid"


class UniversalToolDefinition(BaseModel):
    """The canonical representation of a tool, regardless of implementation."""

    model_config = ConfigDict(frozen=True)

    # Identity (§7): stable, deterministic, version-aware. Not a display name.
    id: str
    name: str
    namespace: ToolNamespace
    provider: str
    version: str = "1"
    definition_version: int = 1  # schema evolution without breaking old trajectories (§58)

    description: str = ""

    # Capability-space semantics: what it does, in which operations.
    capability: str | None = None
    operations: tuple[str, ...] = ()

    # I/O contracts.
    input_schema: ToolInputSchema = Field(default_factory=ToolInputSchema)
    output_schema: dict[str, Any] | None = None

    # Execution + provenance.
    execution_type: ExecutionType
    adapter: str  # adapter kind registered for this tool ("native", "capability", ...)
    provenance: ToolProvenance
    locality: Locality = Locality.LOCAL

    # Safety seat: the (tool, operation) name the permissions manifest and the
    # SafetyEngine know this tool by. The engine stays authoritative (§12/§26).
    safety_tool: str

    policy: ToolPolicyMetadata = Field(default_factory=ToolPolicyMetadata)

    # Routing priors (static; live latency/health arrive in Part 8).
    estimated_cost_usd: float = 0.0  # per typical call
    estimated_latency_ms: int = 500  # p50 prior

    tags: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)  # transport/annotations provenance (Part 5)

    # Convenience views over the policy descriptor (single source of truth).
    @property
    def default_tier(self) -> Tier:
        return self.policy.default_tier

    @property
    def side_effects(self) -> bool:
        return self.policy.side_effects

    @property
    def requires_auth(self) -> bool:
        return self.policy.requires_auth

    @property
    def cost_class(self) -> CostClass:
        return self.policy.cost_class

    @field_validator("id")
    @classmethod
    def _id_must_be_canonical(cls, value: str) -> str:
        parse_tool_id(value)  # raises ValueError on malformed identity
        return value

    @model_validator(mode="after")
    def _identity_must_be_derived(self) -> UniversalToolDefinition:
        expected = build_tool_id(self.namespace, self.provider, self.name)
        if self.id != expected:
            raise ValueError(f"id {self.id!r} does not match canonical {expected!r}")
        if not self.operations:
            raise ValueError("a tool definition must declare at least one operation")
        return self
