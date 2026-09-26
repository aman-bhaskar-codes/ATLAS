"""ATLAS MCP runtime — the official SDK behind an ATLAS-owned boundary
(Part 5).

``atlas.tooling.mcp`` isolates the external `mcp` SDK: everything outside this
package depends on `MCPServerManager` and the ATLAS models (`UniversalTool
Definition`, `UniversalToolResult`), never on SDK types (§4/§131). MCP is an
integration protocol, not ATLAS's architecture: the ToolCatalog stays the
universal discovery layer, the ToolRouter the routing layer, the
ExecutionEngine the execution layer, and the SafetyEngine the authority
(§140).
"""

from __future__ import annotations

from atlas.tooling.mcp.config import load_mcp_server_definitions
from atlas.tooling.mcp.connection import MCPConnection
from atlas.tooling.mcp.errors import (
    MCPAuthError,
    MCPConfigError,
    MCPConnectionError,
    MCPDisabledError,
    MCPDiscoveryError,
    MCPError,
    MCPSecurityError,
    MCPTimeoutError,
    MCPToolCallError,
)
from atlas.tooling.mcp.events import TOPIC_MCP, MCPEvent, MCPEventPublisher
from atlas.tooling.mcp.manager import MCPServerManager
from atlas.tooling.mcp.models import (
    AuthConfig,
    ConnectionState,
    MCPPromptDescriptor,
    MCPResourceDescriptor,
    MCPServerDefinition,
    MCPServerInfo,
    MCPToolDescriptor,
    ReconnectPolicy,
    StartupMode,
    TransportType,
    TrustLevel,
    transition_connection,
)
from atlas.tooling.mcp.security import EndpointPolicy, EnvironmentPolicy, StdioCommandPolicy

__all__ = [
    "TOPIC_MCP",
    "AuthConfig",
    "ConnectionState",
    "EndpointPolicy",
    "EnvironmentPolicy",
    "MCPAuthError",
    "MCPConfigError",
    "MCPConnection",
    "MCPConnectionError",
    "MCPDisabledError",
    "MCPDiscoveryError",
    "MCPError",
    "MCPEvent",
    "MCPEventPublisher",
    "MCPPromptDescriptor",
    "MCPResourceDescriptor",
    "MCPSecurityError",
    "MCPServerDefinition",
    "MCPServerInfo",
    "MCPServerManager",
    "MCPTimeoutError",
    "MCPToolCallError",
    "MCPToolDescriptor",
    "ReconnectPolicy",
    "StartupMode",
    "StdioCommandPolicy",
    "TransportType",
    "TrustLevel",
    "load_mcp_server_definitions",
    "transition_connection",
]
