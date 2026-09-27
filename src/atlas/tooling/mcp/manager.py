"""MCPServerManager — the ATLAS-owned MCP runtime boundary (Part 5 §129-§132).

Owns server definitions, connections, discovery, catalog/registry
synchronization, dynamic change handling (debounced, §42), and shutdown
(§66). Everything outside `atlas.tooling.mcp` depends on THIS manager and the
ATLAS models — the official SDK types never leak (§4/§131).
"""

from __future__ import annotations

import asyncio
from typing import Any

from atlas.infra.config import ExecutionCfg  # noqa: F401 — re-exported type only
from atlas.infra.logging import get_logger
from atlas.tooling.mcp.catalog import MCPCatalogSource, MCPToolAdapter, discover_and_normalize
from atlas.tooling.mcp.connection import MCPConnection
from atlas.tooling.mcp.errors import MCPDisabledError, MCPError
from atlas.tooling.mcp.events import MCPEventPublisher
from atlas.tooling.mcp.models import (
    ConnectionState,
    MCPServerDefinition,
    MCPServerInfo,
)
from atlas.tooling.models.tool_definition import UniversalToolDefinition
from atlas.tooling.registry.registry import ToolingRegistry

_log = get_logger("atlas.tooling.mcp.manager")

_DEFAULT_REFRESH_DEBOUNCE_S = 2.0


class MCPServerManager:
    def __init__(
        self,
        *,
        definitions: list[MCPServerDefinition],
        tooling_registry: ToolingRegistry,
        identity: Any | None = None,
        bus: Any | None = None,
        catalog_refresher: Any | None = None,  # async (source_id) -> None — Part-2 catalog hook (§41)
        refresh_debounce_s: float = _DEFAULT_REFRESH_DEBOUNCE_S,
        command_policy: Any | None = None,
        safety: Any | None = None,  # SafetyEngine — the single funnel every MCP call passes (§53/§74)
    ) -> None:
        self._definitions = {d.server_id: d for d in definitions}
        self._registry = tooling_registry
        self._identity = identity
        self._catalog_refresher = catalog_refresher
        self._refresh_debounce_s = refresh_debounce_s
        self._publisher = MCPEventPublisher(bus)
        self._connections: dict[str, MCPConnection] = {}
        self._live_definitions: dict[str, list[UniversalToolDefinition]] = {}
        self._refresh_tasks: dict[str, asyncio.Task[None]] = {}
        self._mcp_adapter = MCPToolAdapter(self, safety=safety)
        self._shutting_down = False

    # ── Definitions / config (§7-§8) ──────────────────────────────── #

    def definitions(self) -> list[MCPServerDefinition]:
        return list(self._definitions.values())

    def definition(self, server_id: str) -> MCPServerDefinition:
        if server_id not in self._definitions:
            raise MCPError(f"unknown MCP server {server_id!r}")
        return self._definitions[server_id]

    def set_enabled(self, server_id: str, enabled: bool) -> None:
        """§125: enable/disable keeps config+history+credentials."""
        definition = self._definitions[server_id]
        self._definitions[server_id] = definition.model_copy(update={"enabled": enabled})
        if not enabled:
            connection = self._connections.get(server_id)
            if connection is not None:
                connection.disable()
                self._unregister_server_tools(server_id)

    # ── Connection lifecycle (§63-§67/§122-§125) ──────────────────── #

    def connection(self, server_id: str) -> MCPConnection:
        definition = self.definition(server_id)
        connection = self._connections.get(server_id)
        if connection is None:
            connection = MCPConnection(
                definition,
                identity=self._identity,
                publisher=self._publisher,
                command_policy=command_policy_for(definition),
                on_list_changed=self.request_refresh,
            )
            self._connections[server_id] = connection
        return connection

    async def connect(self, server_id: str) -> MCPServerInfo:
        """§64/§65: connect (+discover if eager-path) — manual or on-demand."""
        definition = self.definition(server_id)
        if not definition.enabled:
            raise MCPDisabledError(f"server {server_id!r} is disabled")
        connection = self.connection(server_id)
        info = await connection.connect()
        # discovery completes before READY-serving (§64): refresh immediately
        await self.refresh(server_id)
        _ = info
        return connection.info  # type: ignore[return-value]

    async def disconnect(self, server_id: str) -> None:
        """§123: close transport, keep catalog history + credentials."""
        connection = self._connections.get(server_id)
        if connection is not None:
            await connection.disconnect()
        self._unregister_server_tools(server_id)

    async def remove(self, server_id: str) -> None:
        """§124: disconnect + disable + drop runtime registration. Catalog
        history/audit records are retained (§124)."""
        await self.disconnect(server_id)
        self._unregister_server_tools(server_id)
        self._definitions.pop(server_id, None)
        self._connections.pop(server_id, None)
        self._live_definitions.pop(server_id, None)

    async def ensure_ready(self, server_id: str) -> MCPConnection:
        """§65: on-demand connect for lazy servers, used before execution."""
        definition = self.definition(server_id)
        if not definition.enabled:
            raise MCPDisabledError(f"server {server_id!r} is disabled")
        connection = self.connection(server_id)
        if connection.state != ConnectionState.READY:
            await self.connect(server_id)
        return connection

    # ── Discovery + synchronization (§32-§44/§105-§107) ───────────── #

    async def refresh(self, server_id: str) -> list[UniversalToolDefinition]:
        """connect (if needed) → paginated discovery → normalize → registry +
        Part-2 catalog sync. Discovery failure keeps the last known catalog
        (§43) — never erases tools."""
        connection = await self.ensure_ready(server_id)
        try:
            raw_tools = await connection.discover_tools()
        except Exception as exc:
            connection.counters["discovery_failures"] += 1
            _log.warning(
                "mcp.discovery.failed",
                event_type="mcp",
                server_id=server_id,
                error=repr(exc),
            )
            if connection.state == ConnectionState.READY:
                try:
                    connection.state = ConnectionState.DEGRADED
                except Exception:
                    pass
            await self._publisher.publish("mcp.discovery.failed", server_id=server_id, error=str(exc))
            return self._live_definitions.get(server_id, [])  # §43: retain last known
        if connection.info is None:
            raise MCPError(f"server {server_id!r} has no negotiated info")
        await self._publisher.publish("mcp.discovery.started", server_id=server_id)
        definitions = discover_and_normalize(self.definition(server_id), connection.info, raw_tools)
        self._live_definitions[server_id] = definitions
        self._sync_registry(server_id, definitions)
        await self._publisher.publish("mcp.discovery.completed", server_id=server_id, tools=len(definitions))
        if self._catalog_refresher is not None:
            try:
                await self._catalog_refresher(connection.definition.source_id)
            except Exception as exc:
                _log.warning("mcp.catalog.refresh_failed", event_type="mcp", server_id=server_id, error=repr(exc))
        return definitions

    def _sync_registry(self, server_id: str, definitions: list[UniversalToolDefinition]) -> None:
        """§41: live ToolingRegistry update — no ATLAS restart (§105/§106)."""
        desired = {d.id for d in definitions}
        current = {
            d.id for d in self._registry.list_definitions() if d.namespace.value == "mcp" and d.provider == server_id
        }
        for stale_id in current - desired:
            try:
                self._registry.unregister(stale_id)
            except Exception:
                pass
        for definition in definitions:
            if self._registry.get(definition.id) is None:
                self._registry.register(definition, self._mcp_adapter)

    def _unregister_server_tools(self, server_id: str) -> None:
        for definition in [
            d for d in self._registry.list_definitions() if d.namespace.value == "mcp" and d.provider == server_id
        ]:
            try:
                self._registry.unregister(definition.id)
            except Exception:
                pass
        self._live_definitions.pop(server_id, None)

    # ── Dynamic change handling (§40-§42/§88) ─────────────────────── #

    def request_refresh(self, server_id: str) -> None:
        """§40/§42: list-changed notification → DEBOUNCED refresh."""
        if self._shutting_down or server_id not in self._definitions:
            return
        existing = self._refresh_tasks.pop(server_id, None)
        if existing is not None and not existing.done():
            existing.cancel()
        self._refresh_tasks[server_id] = asyncio.ensure_future(self._debounced_refresh(server_id))

    async def _debounced_refresh(self, server_id: str) -> None:
        try:
            await asyncio.sleep(self._refresh_debounce_s)
            await self.refresh(server_id)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            _log.warning("mcp.refresh.debounce_failed", event_type="mcp", server_id=server_id, error=repr(exc))

    # ── Queries + invocation (§129) ───────────────────────────────── #

    def status(self, server_id: str) -> dict[str, Any]:
        definition = self.definition(server_id)
        connection = self._connections.get(server_id)
        state = connection.state.value if connection else ConnectionState.CONFIGURED.value
        return {
            "server_id": server_id,
            "name": definition.name or server_id,
            "transport": definition.transport.value,
            "enabled": definition.enabled,
            "startup": definition.startup.value,
            "trust_level": definition.trust_level.value,
            "state": state,
            "connected": state == ConnectionState.READY.value,
            "tools": len(self._live_definitions.get(server_id, [])),
            "health": connection.health() if connection else None,
            "protocol_version": connection.info.protocol_version if connection and connection.info else "",
            "server_name": connection.info.server_name if connection and connection.info else "",
            "server_version": connection.info.server_version if connection and connection.info else "",
            "instructions_present": bool(connection.info.instructions if connection and connection.info else False),
            "last_success": (
                connection.last_success_at.isoformat() if connection and connection.last_success_at else None
            ),
            "last_failure": (connection.last_failure if connection else None),
        }

    def status_all(self) -> list[dict[str, Any]]:
        return [self.status(server_id) for server_id in self._definitions]

    def list_tools(self, server_id: str) -> list[UniversalToolDefinition]:
        return list(self._live_definitions.get(server_id, []))

    def catalog_source(self, server_id: str) -> MCPCatalogSource:
        trust = self.definition(server_id).trust_level.value
        return MCPCatalogSource(self._live_definitions.get(server_id, []), server_id=server_id, trust_level=trust)

    async def call_tool(self, server_id: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """§53/§65/§129: ensure the lazy server is up, then tools/call. The
        SAFETY gate lives in the governed path that reaches this method."""
        result = await self.call_tool_normalized(server_id, tool_name, arguments)
        dumped: dict[str, Any] = dict(result.model_dump(mode="json"))
        return dumped

    async def call_tool_normalized(self, server_id: str, tool_name: str, arguments: dict[str, Any]) -> Any:
        """§74: the governed execution path — returns a normalized
        UniversalToolResult for the Part-1 adapter contract."""
        import time as _time

        from atlas.tooling.mcp.normalization import normalize_call_result

        connection = await self.ensure_ready(server_id)
        started = _time.perf_counter()
        raw = await connection.call_tool(tool_name, arguments)
        duration_ms = int((_time.perf_counter() - started) * 1000)
        return normalize_call_result(
            self.definition(server_id),
            connection.info,  # type: ignore[arg-type]
            tool_name,
            raw,
            duration_ms=duration_ms,
        )

    # ── Startup + shutdown (§63-§64/§66) ──────────────────────────── #

    async def start_eager(self) -> None:
        for definition in self._definitions.values():
            if definition.enabled and definition.startup.value == "eager":
                try:
                    await self.connect(definition.server_id)
                except Exception as exc:
                    _log.error(
                        "mcp.eager.failed",
                        event_type="mcp",
                        server_id=definition.server_id,
                        error=repr(exc),
                    )

    async def shutdown(self) -> None:
        """§66: stop refreshers → close sessions → terminate children (via the
        SDK contexts) → no orphaned stdio processes."""
        self._shutting_down = True
        for task in self._refresh_tasks.values():
            task.cancel()
        self._refresh_tasks.clear()
        for connection in list(self._connections.values()):
            try:
                await connection.disconnect()
            except Exception as exc:
                _log.warning("mcp.shutdown.error", event_type="mcp", error=repr(exc))
        self._connections.clear()


def command_policy_for(definition: MCPServerDefinition) -> Any:
    """§12: per-server command policy. The allowlist is intentionally tiny by
    default (npx/uvx/python3 cover the standard MCP server distribution
    channels); operators can widen via config later."""
    from atlas.tooling.mcp.security import StdioCommandPolicy

    return StdioCommandPolicy(allowed_executables=("npx", "uvx", "python3", "python", "docker"))


__all__ = ["MCPServerManager", "command_policy_for"]
