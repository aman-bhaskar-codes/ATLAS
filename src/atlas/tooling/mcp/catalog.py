"""MCP tool discovery + catalog/registry integration (Part 5 §32-§44/§73-§74).

Discovery normalizes paginated `tools/list` results into universal
definitions; the manager registers them into the Part-1 `ToolingRegistry`
(with the governed MCPToolAdapter) so Part-3 routing and Part-4 execution see
MCP tools exactly like every other tool (§73/§74). Catalog synchronization is
delegated to the Part-2 `ToolCatalog` through an `MCPCatalogSource`.
"""

from __future__ import annotations

from typing import Any

from atlas.tooling.mcp.errors import MCPDiscoveryError
from atlas.tooling.mcp.models import MCPServerDefinition, MCPServerInfo
from atlas.tooling.mcp.normalization import normalize_tool
from atlas.tooling.models.tool_definition import UniversalToolDefinition


class MCPToolAdapter:
    """Part-1 `ToolAdapter` protocol implementation for MCP tools (§53/§74).

    Execution flows: ExecutionEngine → ToolingExecutor → (this adapter) →
    MCPServerManager.call_tool → SDK tools/call. The SafetyEngine gate happens
    BEFORE this adapter is reached — MCP never bypasses it (§53).
    """

    kind = "mcp"

    def __init__(self, manager: Any) -> None:
        self._manager = manager

    async def initialize(self) -> None:
        return None  # connections are managed by MCPServerManager lifecycle

    async def validate(self, definition: UniversalToolDefinition) -> None:
        if not definition.id.startswith("mcp:"):
            raise ValueError(f"not an MCP tool id: {definition.id!r}")

    async def execute(
        self,
        invocation: Any,  # UniversalToolInvocation
        definition: UniversalToolDefinition,
    ) -> Any:
        tool_name = definition.name
        server_id = definition.provider
        arguments = dict(invocation.arguments.get("arguments") or invocation.arguments)
        if "operation" in arguments and len(arguments) == 1:
            arguments = {}
        return await self._manager.call_tool_normalized(server_id, tool_name, arguments)

    async def health(self) -> bool:
        return True  # per-server health lives on the connection (§60)

    async def shutdown(self) -> None:
        return None


class MCPCatalogSource:
    """Part-2 `CatalogSource` for one connected MCP server (§41)."""

    def __init__(self, definitions: list[UniversalToolDefinition], *, server_id: str, trust_level: str) -> None:
        self._definitions = list(definitions)
        self.source_id = server_id if ":" in server_id else f"mcp:{server_id}"
        self.source_type = "mcp"
        self.display_name = f"MCP server {self.source_id.split(':', 1)[1]!r}"
        self.description = "Dynamically discovered MCP tools."
        self.version = "1"
        self.trust_level = trust_level
        from atlas.tooling.models.tool_definition import Locality

        self.locality = Locality.LOCAL

    async def discover(self) -> list[UniversalToolDefinition]:
        return list(self._definitions)


def discover_and_normalize(
    definition: MCPServerDefinition,
    info: MCPServerInfo,
    raw_tools: list[Any],
) -> list[UniversalToolDefinition]:
    """§34-§36/§85: normalize discovered tools; duplicate names from one server
    are an anomaly — the FIRST is kept, the duplicate recorded (never silently
    merged, never overwriting another server's tools, §84)."""
    from atlas.infra.logging import get_logger

    log = get_logger("atlas.tooling.mcp.discovery")
    by_name: dict[str, Any] = {}
    duplicates: list[str] = []
    for tool in raw_tools:
        if tool.name in by_name:
            duplicates.append(tool.name)
            continue
        by_name[tool.name] = tool
    if duplicates:
        log.warning(
            "mcp.discovery.duplicate_tools",
            event_type="mcp",
            server_id=definition.server_id,
            duplicates=duplicates,
        )
    if not by_name and raw_tools:
        raise MCPDiscoveryError(f"server {definition.server_id!r} returned only duplicate tool names")
    return [normalize_tool(definition, info, tool) for tool in by_name.values()]


__all__ = ["MCPCatalogSource", "MCPToolAdapter", "discover_and_normalize"]
