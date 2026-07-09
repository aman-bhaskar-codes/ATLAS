"""MCP provider base — the transport that makes any MCP server a Provider.

WHY a base class (not per-server code): every MCP server speaks the same JSON-RPC
(tools/list, tools/call) over stdio or HTTP. This base owns the handshake, tool
discovery, and call plumbing. A concrete MCP-backed provider subclasses it, pins
the server command/URL + the tool name for its operation, and implements only
normalize(). Adding an MCP server = a tiny subclass + config, never new transport
code. (Full JSON-RPC client fleshed out in 6.9 alongside the comms MCP work; this
is the stable seam + a structured stub so the capability core is MCP-ready now.)
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from atlas.capabilities.errors import ProviderExecutionError
from atlas.capabilities.providers.base import (
    CapabilityRequest,
    RetryPolicy,
)
from atlas.capabilities.registry.capability import Capability


class MCPServerConfig(BaseModel):
    model_config = {"frozen": True}
    name: str
    transport: str            # 'stdio' | 'http'
    command: tuple[str, ...] = ()   # for stdio, e.g. ('npx','-y','@modelcontextprotocol/server-filesystem')
    url: str | None = None          # for http
    tool_map: dict[str, str] = {}   # capability operation -> MCP tool name


class MCPProvider:
    """Base for MCP-backed providers. Subclasses set `capability` and `normalize`.
    is_local reflects whether the MCP server runs on-device (stdio) vs remote.
    """

    requires_auth = False   # most local MCP servers are unauthenticated; override if not

    def __init__(self, config: MCPServerConfig, capability: Capability) -> None:
        self.name = f"mcp:{config.name}"
        self.capability = capability
        self._config = config
        self.is_local = config.transport == "stdio"
        self._client: Any = None   # JSON-RPC client, opened in initialize()

    async def initialize(self) -> None:
        # 6.9 fills in the JSON-RPC client (stdio subprocess or HTTP session)
        # + the MCP `initialize` handshake and tools/list discovery.
        self._client = _NullMCPClient(self._config)
        await self._client.open()

    async def authenticate(self) -> None:
        return None

    async def health(self) -> bool:
        return self._client is not None and await self._client.ping()

    async def execute(self, request: CapabilityRequest) -> Any:
        tool = self._config.tool_map.get(request.operation)
        if tool is None:
            raise ProviderExecutionError(
                f"{self.name}: no MCP tool mapped for operation {request.operation!r}")
        return await self._client.call_tool(tool, request.args)

    def normalize(self, raw: Any) -> BaseModel:
        raise NotImplementedError("concrete MCP provider must map tool result -> domain model")

    def retry_policy(self) -> RetryPolicy:
        return RetryPolicy()

    async def shutdown(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None


class _NullMCPClient:
    """Placeholder JSON-RPC client. Real stdio/HTTP client lands in 6.9.
    WHY ship it now: keeps MCPProvider importable + testable and pins the exact
    client surface (open/ping/call_tool/close) 6.9 must implement."""

    def __init__(self, config: MCPServerConfig) -> None:
        self._config = config

    async def open(self) -> None: ...
    async def ping(self) -> bool:
        return True
    async def call_tool(self, tool: str, args: dict[str, Any]) -> Any:
        raise ProviderExecutionError("MCP client not implemented until Part 6.9")
    async def close(self) -> None: ...
