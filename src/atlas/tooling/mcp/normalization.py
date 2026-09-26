"""MCP → ATLAS normalization (Part 5 §34-§39/§56/§59).

SDK types NEVER leak outside `atlas.tooling.mcp` (§4/§131): MCP tools become
Part-1 `UniversalToolDefinition`s with stable identity ``mcp:<server_id>:<tool>
`` (§35), and results become `UniversalToolResult`s preserving content-block
semantics and MCP provenance (§56/§59).

Trust-aware defaults (§37/§94): annotations are UNTRUSTED hints — they are
preserved as metadata, but conservative policy (side effects possible,
descriptor tier CONFIRM) is applied unless the server is TRUSTED_REMOTE and
the tool declares read_only_hint. The SafetyEngine remains authoritative.
"""

from __future__ import annotations

import time
from typing import Any

from atlas.capabilities.domain.common import SourceKind
from atlas.infra.types import CostClass, PrivacyClass, Tier
from atlas.tooling.mcp.models import MCPServerDefinition, MCPServerInfo, TrustLevel
from atlas.tooling.models.identity import ToolNamespace
from atlas.tooling.models.tool_definition import ExecutionType, Locality, UniversalToolDefinition
from atlas.tooling.models.tool_policy import ToolPolicyMetadata
from atlas.tooling.models.tool_provenance import ToolProvenance
from atlas.tooling.models.tool_result import FailureKind, ToolFailure, UniversalToolResult
from atlas.tooling.models.tool_schema import ToolInputSchema


def normalize_tool(
    definition: MCPServerDefinition,
    info: MCPServerInfo,
    tool: Any,  # mcp.types.Tool — the SDK type stays inside this package (§131)
) -> UniversalToolDefinition:
    """§34: map one discovered MCP tool to the universal definition."""
    tool_id = f"mcp:{definition.server_id}:{tool.name}"
    trusted = definition.trust_level == TrustLevel.TRUSTED_REMOTE
    annotations = _annotations_of(tool)
    # §94: hints NEVER elevate policy. Conservative default: side effects
    # possible → descriptor tier CONFIRM. read_only_hint only informs metadata
    # for trusted servers — SafetyEngine stays authoritative.
    read_only = trusted and bool(annotations.get("read_only_hint"))
    policy = ToolPolicyMetadata(
        default_tier=Tier.AUTO if read_only else Tier.CONFIRM,
        side_effects=not read_only,
        supports_rollback=False,
        idempotent=bool(annotations.get("idempotent_hint", False)),
        requires_auth=definition.auth.mode != "none",
        max_privacy=PrivacyClass.PUBLIC,
        network_required=definition.transport != "stdio",
        cost_class=CostClass.FREE,  # user-configured servers; quotas arrive in Part 7/8 (§79)
        trust_level=definition.trust_level.value,
    )
    return UniversalToolDefinition(
        id=tool_id,
        name=tool.name,
        namespace=ToolNamespace.MCP,
        provider=definition.server_id,
        description=(tool.description or tool.title or f"MCP tool {tool.name!r} on server {definition.server_id!r}."),
        operations=("call",),  # §36: one MCP tool = one operation ("call")
        input_schema=_input_schema_of(tool),
        execution_type=ExecutionType.MCP,
        adapter="mcp",
        provenance=ToolProvenance(
            source_kind=SourceKind.MCP,
            provider=definition.server_id,
            uri=definition.url,
        ),
        locality=Locality.LOCAL if definition.transport == "stdio" else Locality.REMOTE,
        safety_tool="mcp",  # the manifest seat every MCP tool shares
        policy=policy,
        estimated_latency_ms=250 if definition.transport == "stdio" else 750,
        tags=("mcp", definition.server_id, *definition.tags),
        metadata={
            "title": tool.title or "",
            "annotations": annotations,  # preserved, UNTRUSTED (§37)
            "server_protocol_version": info.protocol_version,
            "server_transport": definition.transport.value,
            "trust_level": definition.trust_level.value,
        },
    )


def _annotations_of(tool: Any) -> dict[str, Any]:
    raw = getattr(tool, "annotations", None)
    if raw is None:
        return {}
    dumped = raw.model_dump(exclude_none=True) if hasattr(raw, "model_dump") else dict(raw)
    return {k: v for k, v in dumped.items() if v is not None}


def _input_schema_of(tool: Any) -> ToolInputSchema:
    """§38: persist the EXACT normalized JSON Schema (2020-12); unknown
    extensions preserved; validated object-shaped before registration."""
    raw = getattr(tool, "input_schema", None)
    if raw is None:
        return ToolInputSchema.from_operations(("call",))
    schema: dict[str, Any] = raw.model_dump(mode="json") if hasattr(raw, "model_dump") else dict(raw)
    if not schema:
        return ToolInputSchema.from_operations(("call",))
    if schema.get("type") not in (None, "object"):
        # §38: a non-object input schema is preserved for diagnostics; the
        # fallback operation schema governs invocation.
        return ToolInputSchema.from_operations(("call",))
    return ToolInputSchema(operations={"call": schema})


def _output_schema_of(tool: Any) -> dict[str, Any] | None:
    """§39: persist when provided; never required."""
    raw = getattr(tool, "output_schema", None)
    if raw is None:
        return None
    dumped = raw.model_dump(mode="json") if hasattr(raw, "model_dump") else raw
    return dict(dumped) if dumped else None


def normalize_call_result(
    definition: MCPServerDefinition,
    info: MCPServerInfo,
    tool_name: str,
    result: Any,  # mcp.types.CallToolResult (§131: the SDK type stays inside)
    duration_ms: int,
) -> UniversalToolResult:
    """§56/§59: normalize a tools/call result preserving content-block types,
    structured content, error flag, and full MCP provenance (§59)."""
    content_blocks = [
        block.model_dump(mode="json") if hasattr(block, "model_dump") else {"raw": str(block)}
        for block in (getattr(result, "content", None) or ())
    ]
    text_parts = [b.get("text", "") for b in content_blocks if isinstance(b, dict) and b.get("type") == "text"]
    structured = getattr(result, "structured_content", None)
    is_error = bool(getattr(result, "is_error", False))
    if structured is not None:
        data: Any = structured.model_dump(mode="json") if hasattr(structured, "model_dump") else structured
    elif content_blocks:
        data = "\n".join(text_parts) if text_parts else content_blocks
    else:
        data = None
    provenance = {
        "source_kind": "mcp",
        "server_id": definition.server_id,
        "server_version": info.server_version,
        "protocol_version": info.protocol_version,
        "tool_name": tool_name,
        "transport": definition.transport.value,
        "timestamp": time.time(),
    }
    return UniversalToolResult(
        ok=not is_error,
        tool_id=f"mcp:{definition.server_id}:{tool_name}",
        provider=definition.server_id,
        data=data,
        error=None
        if not is_error
        else ToolFailure(
            kind=FailureKind.EXECUTION_ERROR,
            message="\n".join(text_parts) or "MCP tool call reported is_error",
        ),
        duration_ms=duration_ms,
        metadata={"provenance": provenance, "content_blocks": content_blocks},
    )


__all__ = ["normalize_call_result", "normalize_tool"]
