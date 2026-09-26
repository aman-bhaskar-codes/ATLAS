"""Concrete Part-2 catalog sources.

Both read from the live Part-1 ``ToolingRegistry`` — the registry remains the
authority on what exists; the catalog is the durable discovery index over it
(Part 2 §36). An MCP source (Part 5) will implement the same
``CatalogSource`` protocol without any catalog change (§40).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from atlas.tooling.models.tool_definition import UniversalToolDefinition
from atlas.tooling.models.tool_health import ToolRuntimeState, ToolStatus
from atlas.tooling.registry.registry import ToolingRegistry


class RegistryCatalogSource:
    """All live definitions for one namespace:provider pair (e.g.
    ``native:atlas``). Reports runtime status so catalog lifecycle reflects
    reality (READY/DISCOVERED/DISABLED/UNAVAILABLE)."""

    def __init__(self, registry: ToolingRegistry, *, namespace: str) -> None:
        self._registry = registry
        self._namespace = namespace
        self.source_id = f"{namespace}:atlas"
        self.source_type = namespace
        self.display_name = f"ATLAS {namespace} tools"
        self.description = f"Tools discovered from the live ATLAS {namespace} tooling registry."
        self.version = "1"
        self.trust_level = "system_builtin"
        from atlas.tooling.models.tool_definition import Locality

        self.locality = Locality.LOCAL if namespace == "native" else Locality.REMOTE

    async def discover(self) -> Sequence[UniversalToolDefinition]:
        return [
            definition
            for definition in self._registry.list_definitions()
            if definition.namespace.value == self._namespace
        ]

    def runtime_status(self, tool_id: str) -> ToolStatus | None:
        registration = self._registry.get(tool_id)
        return registration.status if registration else None


def default_sources(registry: ToolingRegistry) -> list[Any]:
    """The Part-2 source set: native + capability + MCP (one source per MCP
    server, §35/§36), filtered to what the live registry actually contains (a
    namespace with zero tools is skipped so the catalog never fabricates an
    empty source)."""
    namespaces = {definition.namespace.value for definition in registry.list_definitions()}
    sources: list[Any] = [RegistryCatalogSource(registry, namespace=ns) for ns in sorted(namespaces) if ns != "mcp"]
    # §35: one catalog source PER MCP SERVER — `mcp:<server_id>` — so two
    # servers exposing the same tool name never collide (§84).
    mcp_providers = sorted({d.provider for d in registry.list_definitions() if d.namespace.value == "mcp"})
    sources.extend(MCPRegistrySource(registry, server_id=pid) for pid in mcp_providers)
    return sources


__all__ = ["RegistryCatalogSource", "default_sources"]


class MCPRegistrySource:
    """Catalog source for one connected MCP server's live definitions
    (Part 5 §35/§41). Reads from the ToolingRegistry, which the MCPServerManager
    keeps synchronized."""

    def __init__(self, registry: ToolingRegistry, *, server_id: str) -> None:
        self._registry = registry
        self._server_id = server_id
        self.source_id = f"mcp:{server_id}"
        self.source_type = "mcp"
        self.display_name = f"MCP server {server_id!r}"
        self.description = "Dynamically discovered MCP tools (official SDK runtime)."
        self.version = "1"
        self.trust_level = "remote_user_configured"
        from atlas.tooling.models.tool_definition import Locality

        self.locality = Locality.REMOTE

    async def discover(self) -> list[UniversalToolDefinition]:
        return [
            d for d in self._registry.list_definitions() if d.namespace.value == "mcp" and d.provider == self._server_id
        ]

    def runtime_status(self, tool_id: str) -> Any:
        """Live registry state → READY catalog state (live MCP tools are
        executable through the governed adapter path)."""
        from atlas.tooling.models.tool_health import ToolStatus

        return ToolStatus(state=ToolRuntimeState.READY, detail="live MCP connection")


__all__ = ["MCPRegistrySource", "RegistryCatalogSource", "default_sources"]
