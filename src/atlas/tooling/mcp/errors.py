"""MCP error taxonomy (Part 5 §4/§131).

All MCP failures are typed so the recovery/routing layers can branch on
class, not message text. Rooted at AtlasError like every ATLAS error.
"""

from __future__ import annotations

from atlas.infra.errors import AtlasError


class MCPError(AtlasError):
    """Root of MCP runtime errors."""

    code = "mcp.error"


class MCPConfigError(MCPError):
    """Invalid server definition/config (bad transport, missing url/command)."""

    code = "mcp.config"


class MCPConnectionError(MCPError):
    """Transport/session failure (process died, HTTP disconnected)."""

    code = "mcp.connection"
    retryable = True


class MCPTimeoutError(MCPError):
    code = "mcp.timeout"
    retryable = True


class MCPAuthError(MCPError):
    """Missing/invalid/expired credentials for a server (§68)."""

    code = "mcp.auth"


class MCPDiscoveryError(MCPError):
    """tools/list or schema validation failure — the server is connected but
    its tools are unavailable (§44)."""

    code = "mcp.discovery"


class MCPProtocolError(MCPError):
    """Non-protocol output on stdout / malformed protocol data (§14)."""

    code = "mcp.protocol"


class MCPToolCallError(MCPError):
    """The server reported the tool call failed (is_error) or crashed mid-call."""

    code = "mcp.tool_call"
    retryable = True


class MCPDisabledError(MCPError):
    """Server disabled: no connect, no execution (§125)."""

    code = "mcp.disabled"


class MCPSecurityError(MCPError):
    """A security policy rejected the operation (command, endpoint, env) (§12/§20)."""

    code = "mcp.security"


__all__ = [
    "MCPAuthError",
    "MCPConfigError",
    "MCPConnectionError",
    "MCPDisabledError",
    "MCPDiscoveryError",
    "MCPError",
    "MCPProtocolError",
    "MCPSecurityError",
    "MCPTimeoutError",
    "MCPToolCallError",
]
