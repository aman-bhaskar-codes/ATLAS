"""MCP tool discovery + catalog/registry integration (Part 5 §32-§44/§73-§74).

Discovery normalizes paginated `tools/list` results into universal
definitions; the manager registers them into the Part-1 `ToolingRegistry`
(with the governed MCPToolAdapter) so Part-3 routing and Part-4 execution see
MCP tools exactly like every other tool (§73/§74). Catalog synchronization is
delegated to the Part-2 `ToolCatalog` through an `MCPCatalogSource`.
"""

from __future__ import annotations

import time
from typing import Any

from atlas.infra.types import ToolRequest, ToolResult
from atlas.safety.engine import DeniedError, HaltedError, SafetyEngine
from atlas.tooling.mcp.errors import MCPDiscoveryError
from atlas.tooling.mcp.models import MCPServerDefinition, MCPServerInfo
from atlas.tooling.mcp.normalization import normalize_tool
from atlas.tooling.models.tool_definition import UniversalToolDefinition
from atlas.tooling.models.tool_result import FailureKind, UniversalToolResult


class _GuardedMCPCall:
    """A one-shot `Tool` that runs a single MCP ``tools/call`` when the
    SafetyEngine authorizes it. Holds the produced `UniversalToolResult` so the
    adapter can return it verbatim (preserving MCP provenance/content blocks)
    once ``guard()`` has admitted the call."""

    name = "mcp"

    def __init__(self, manager: Any, server_id: str, tool_name: str) -> None:
        self._manager = manager
        self._server_id = server_id
        self._tool_name = tool_name
        self.result: UniversalToolResult | None = None

    def dry_run(self, args: dict[str, Any]) -> str:
        return f"MCP call {self._server_id!r}:{self._tool_name!r}"

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        arguments = args.get("arguments") or {}
        res: UniversalToolResult = await self._manager.call_tool_normalized(
            self._server_id, self._tool_name, arguments
        )
        self.result = res
        return ToolResult(ok=res.ok, output=res.data, error=(res.error.message if res.error else None))


class MCPToolAdapter:
    """Part-1 `ToolAdapter` protocol implementation for MCP tools (§53/§74).

    Execution flows: ExecutionEngine → ToolingExecutor → (this adapter) →
    SafetyEngine.guard → MCPServerManager.call_tool → SDK tools/call. MCP tools
    are external/remote, so this adapter routes EVERY call through the SAME
    single funnel (`guard()`, manifest seat ``mcp:call``) as the native and
    capability adapters — it never reaches the server directly. If no
    SafetyEngine is wired the adapter fails CLOSED rather than running
    ungoverned (§53, single-funnel Constitution).
    """

    kind = "mcp"

    def __init__(self, manager: Any, *, safety: SafetyEngine | None = None) -> None:
        self._manager = manager
        self._safety = safety

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
        if self._safety is None:
            # Fail closed: an MCP call MUST pass through the funnel. A missing
            # SafetyEngine is a wiring bug, never a licence to run ungoverned.
            return UniversalToolResult.failure(
                tool_id=invocation.tool_id,
                kind=FailureKind.POLICY_DENIED,
                message="MCP execution requires the SafetyEngine funnel; refusing to run ungoverned",
                retryable=False,
            )
        tool_name = definition.name
        server_id = definition.provider
        arguments = dict(invocation.arguments.get("arguments") or invocation.arguments)
        if "operation" in arguments and len(arguments) == 1:
            arguments = {}
        req = ToolRequest(
            correlation_id=invocation.correlation_id,
            tool="mcp",
            operation="call",
            args={"server_id": server_id, "tool_name": tool_name, "arguments": arguments},
        )
        guarded = _GuardedMCPCall(self._manager, server_id, tool_name)
        started = time.perf_counter()
        try:
            await self._safety.guard(req, guarded)
        except DeniedError as exc:
            return UniversalToolResult.failure(
                tool_id=invocation.tool_id,
                kind=FailureKind.POLICY_DENIED,
                message=f"denied (tier {exc.decision.tier.name}): {exc.decision.reason}",
                retryable=False,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        except HaltedError as exc:
            return UniversalToolResult.failure(
                tool_id=invocation.tool_id,
                kind=FailureKind.HALTED,
                message=f"halted: {exc}",
                retryable=False,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        duration_ms = int((time.perf_counter() - started) * 1000)
        result = guarded.result
        if result is None:  # defensive: guard admitted but produced no result
            return UniversalToolResult.failure(
                tool_id=invocation.tool_id,
                kind=FailureKind.EXECUTION_ERROR,
                message="MCP call produced no result after guard admission",
                retryable=True,
                duration_ms=duration_ms,
            )
        return result.model_copy(update={"duration_ms": result.duration_ms or duration_ms})

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
