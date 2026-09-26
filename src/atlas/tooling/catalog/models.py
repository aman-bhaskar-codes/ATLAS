"""Tool catalog domain models.

The catalog is the durable answer to "what tools is ATLAS aware of, what are
their schemas and metadata, where did they come from, when were they last
seen, and what is their current catalog state?" — deliberately distinct from
the live registry's "what can this process execute right now?" (Part 2 §2).

Hierarchy: Source -> Namespace -> Tool -> Operation (§5). Records are pydantic
frozen models; the store persists them normalized (§16), the index serves them
from memory (§53).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from atlas.infra.types import ToolCallSpec
from atlas.tooling.models.tool_definition import (
    ExecutionType,
    Locality,
    UniversalToolDefinition,
)
from atlas.tooling.models.tool_policy import TrustLevel


class CatalogStatus(StrEnum):
    """Catalog lifecycle state — catalog EXISTENCE, never runtime
    executability (§18): a tool can be READY in the catalog while its source
    is down, and UNAVAILABLE while fully cataloged."""

    DISCOVERED = "DISCOVERED"
    VALIDATED = "VALIDATED"
    READY = "READY"
    STALE = "STALE"
    DISABLED = "DISABLED"
    UNAVAILABLE = "UNAVAILABLE"
    REMOVED = "REMOVED"


class AuthState(StrEnum):
    """Credential-state descriptor (§43). The catalog is NOT the credential
    authority — the identity vault is; this is routing metadata only."""

    NONE = "NONE"
    REQUIRED = "REQUIRED"
    AVAILABLE = "AVAILABLE"
    MISSING = "MISSING"
    INVALID = "INVALID"
    UNKNOWN = "UNKNOWN"


class Availability(StrEnum):
    """Separate axis from auth (§44): auth=AVAILABLE + availability=UNAVAILABLE
    is a legitimate combination (creds valid, provider down)."""

    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


class ToolAnnotations(BaseModel):
    """MCP-style behavioral hints. UNTRUSTED by definition (§45): they describe
    what a source CLAIMS about a tool; they never authorize, never tier, never
    bypass the SafetyEngine. For Part-2 sources they are derived from our own
    metadata; for future MCP sources they arrive from the wire and stay hints."""

    model_config = ConfigDict(frozen=True)

    read_only_hint: bool | None = None
    destructive_hint: bool | None = None
    idempotent_hint: bool | None = None
    open_world_hint: bool | None = None


class CatalogSourceInfo(BaseModel):
    """One discovery origin (native runtime, capability platform, later an MCP
    server). Source identity is EXPLICIT (§8) — never parsed from names."""

    model_config = ConfigDict(frozen=True)

    source_id: str  # e.g. "native:atlas", "capability:atlas", "mcp:github"
    source_type: str  # native | capability | mcp | http | ...
    display_name: str = ""
    description: str = ""
    version: str = ""
    trust_level: str = TrustLevel.SYSTEM_BUILTIN
    locality: Locality = Locality.LOCAL
    last_sync_ts: datetime | None = None
    last_sync_ok: bool | None = None


class CatalogNamespaceRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    namespace_id: str
    source_id: str
    name: str
    description: str = ""
    version: str = ""
    status: CatalogStatus = CatalogStatus.READY
    tags: tuple[str, ...] = ()
    tool_count: int = 0  # filled by the index, not stored per-row
    created_ts: datetime | None = None
    updated_ts: datetime | None = None


class CatalogOperationRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    operation_id: str  # "<tool_id>:<name>"
    tool_id: str
    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] | None = None
    schema_fingerprint: str = ""
    annotations: ToolAnnotations = Field(default_factory=ToolAnnotations)
    status: CatalogStatus = CatalogStatus.READY
    created_ts: datetime | None = None
    updated_ts: datetime | None = None


class CatalogToolRecord(BaseModel):
    """The durable canonical catalog record for one tool (§6)."""

    model_config = ConfigDict(frozen=True)

    # Identity + hierarchy.
    tool_id: str
    tool_name: str
    namespace_id: str
    source_id: str
    provider: str
    adapter: str
    version: str = "1"
    definition_version: int = 1

    description: str = ""
    search_text: str = ""  # normalized for lexical search; display uses `description` (§75)

    capability: str | None = None
    operations: tuple[str, ...] = ()

    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] | None = None
    input_schema_fp: str = ""
    output_schema_fp: str = ""
    definition_fp: str = ""

    execution_type: ExecutionType = ExecutionType.NATIVE

    # Lifecycle.
    status: CatalogStatus = CatalogStatus.DISCOVERED
    enabled: bool = True
    availability: Availability = Availability.UNKNOWN

    # Auth (references only — NEVER secret values, §7).
    requires_auth: bool = False
    credential_reference: str | None = None
    auth_state: AuthState = AuthState.NONE

    # Cost / latency priors.
    cost_class: str = "free"
    estimated_cost_usd: float = 0.0
    estimated_latency_ms: int = 500
    schema_bytes: int = 0
    estimated_schema_tokens: int = 0

    # Safety descriptor (SafetyEngine remains authoritative).
    safety_tool: str = ""
    default_tier: int = 0
    side_effects: bool = False
    idempotent: bool = True
    rollback_support: bool = False

    # Data + trust descriptors.
    trust_level: str = TrustLevel.SYSTEM_BUILTIN
    locality: Locality = Locality.LOCAL
    privacy_class: str = "public"
    network_required: bool = False

    tags: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)

    # Timestamps (§19).
    created_ts: datetime | None = None
    updated_ts: datetime | None = None
    last_seen_ts: datetime | None = None
    last_validated_ts: datetime | None = None

    def to_definition(self) -> UniversalToolDefinition:
        """Reconstruct the Part-1 universal definition from catalog fields.
        Used by router preparation (to_tool_specs) and future consumers that
        need the canonical in-memory contract."""
        from atlas.infra.types import CostClass, PrivacyClass, Tier
        from atlas.tooling.models.identity import ToolNamespace
        from atlas.tooling.models.tool_policy import ToolPolicyMetadata
        from atlas.tooling.models.tool_provenance import ToolProvenance
        from atlas.tooling.models.tool_schema import ToolInputSchema

        policy = ToolPolicyMetadata(
            default_tier=Tier(self.default_tier),
            side_effects=self.side_effects,
            supports_rollback=self.rollback_support,
            idempotent=self.idempotent,
            requires_auth=self.requires_auth,
            max_privacy=PrivacyClass(self.privacy_class),
            network_required=self.network_required,
            cost_class=CostClass(self.cost_class),
            trust_level=self.trust_level,
        )
        return UniversalToolDefinition(
            id=self.tool_id,
            name=self.tool_name,
            namespace=ToolNamespace(self.source_id.split(":", 1)[0]),
            provider=self.provider,
            version=self.version,
            definition_version=self.definition_version,
            description=self.description,
            capability=self.capability,
            operations=self.operations,
            input_schema=ToolInputSchema.from_operations(self.operations),
            execution_type=self.execution_type,
            adapter=self.adapter,
            provenance=ToolProvenance(source_kind=self._source_kind(), provider=self.provider),
            locality=self.locality,
            safety_tool=self.safety_tool,
            policy=policy,
            tags=self.tags,
        )

    def _source_kind(self) -> Any:
        from atlas.capabilities.domain.common import SourceKind

        source_type = self.source_id.split(":", 1)[0]
        if source_type == "mcp":
            return SourceKind.MCP
        if self.locality == Locality.LOCAL:
            return SourceKind.LOCAL
        return SourceKind.WEB

    def to_tool_spec(self) -> ToolCallSpec:
        """Provider-native schema for THIS tool (Part 2 §73 bridge). Callers
        select the shortlist; the catalog never exposes everything at once."""
        properties: dict[str, object] = {
            "operation": {"type": "string", "enum": list(self.operations)},
            "args": {"type": "object", "description": "operation arguments"},
        }
        return ToolCallSpec(
            name=self.tool_id,
            description=self.description,
            parameters={"type": "object", "properties": properties, "required": ["operation"]},
        )


# ── Sync machinery (§20-§23, §52) ──────────────────────────────────────── #


class CatalogSyncError(BaseModel):
    """A structured rejection from one sync (§48/§64)."""

    model_config = ConfigDict(frozen=True)

    tool_id: str
    reason: str


class CatalogSyncResult(BaseModel):
    """Structured per-source sync outcome (§21) — never just a count."""

    model_config = ConfigDict(frozen=True)

    source_id: str
    ok: bool
    discovered: int = 0
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    stale: int = 0
    removed: int = 0
    rejected: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int = 0
    catalog_version: int = 0
    errors: tuple[CatalogSyncError, ...] = ()
    error: str | None = None  # source-level failure (discovery itself failed)


class CatalogDiff(BaseModel):
    """Change set between a source's previous snapshot and fresh discovery
    (§52), compared by stable identity + fingerprints."""

    model_config = ConfigDict(frozen=True)

    added: tuple[str, ...] = ()
    changed: tuple[str, ...] = ()
    unchanged: tuple[str, ...] = ()
    stale: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()


# ── Search (§25-§28) ───────────────────────────────────────────────────── #


class CatalogMatch(BaseModel):
    """One lexical-search hit WITH its explanation (§26). Deterministic lexical
    relevance only — semantic scores arrive in Part 6 on the same contract."""

    model_config = ConfigDict(frozen=True)

    tool_id: str
    score: float
    matched_fields: tuple[str, ...] = ()
    # Part 6 extension points — populated by future semantic retrieval, never faked:
    semantic_score: float | None = None
    embedding_score: float | None = None
    rerank_score: float | None = None

    record: CatalogToolRecord | None = None  # attached by the catalog when serving


class CatalogSnapshot(BaseModel):
    """Logical consistency snapshot (§32): enough to know WHAT the catalog
    looked like (version + shape) for routing traces and tests — not a second
    database copy."""

    model_config = ConfigDict(frozen=True)

    snapshot_id: str
    created_at: datetime
    catalog_version: int
    tool_count: int
    namespace_count: int
    source_count: int
    operation_count: int = 0
    status_counts: dict[str, int] = Field(default_factory=dict)
