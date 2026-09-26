"""MCP bootstrap — the MCP runtime as a composition-root subsystem (Part 5).

Builds the MCPServerManager from `config/mcp.yaml`, wires the Part-2 catalog
refresher (an MCP discovery syncs into the ToolCatalog with no restart, §41),
and registers a ToolingRegistry source so routing/execution treat MCP tools
like every other tool (§73/§74).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from atlas.infra.bus import MessageBus
from atlas.infra.logging import get_logger
from atlas.tooling.catalog.catalog import ToolCatalog
from atlas.tooling.mcp.config import load_mcp_server_definitions
from atlas.tooling.mcp.manager import MCPServerManager
from atlas.tooling.registry.registry import ToolingRegistry

_log = get_logger("atlas.bootstrap.mcp")


@dataclass
class MCPComponents:
    manager: MCPServerManager


def build_mcp(
    *,
    config_dir: Path,
    tooling_registry: ToolingRegistry,
    catalog: ToolCatalog | None,
    bus: MessageBus | None = None,
) -> MCPComponents:
    definitions = load_mcp_server_definitions(config_dir)

    async def catalog_refresher(source_id: str) -> None:
        """§41: an MCP discovery syncs into the Part-2 ToolCatalog — no
        ATLAS restart. Catalog diff/events are the existing Part-2 machinery
        (§72)."""
        if catalog is None:
            return
        await catalog.refresh_source(source_id)

    manager = MCPServerManager(
        definitions=definitions,
        tooling_registry=tooling_registry,
        bus=bus,
        catalog_refresher=catalog_refresher,
    )

    # Warm the catalog: servers configured `enabled: true` with cached
    # definitions are synced lazily on first connect (startup stays cheap, §63).
    if definitions:
        _log.info(
            "mcp.configured",
            event_type="lifecycle",
            servers=[d.server_id for d in definitions],
            eager=[d.server_id for d in definitions if d.startup.value == "eager"],
        )
    return MCPComponents(manager=manager)


__all__ = ["MCPComponents", "build_mcp"]
