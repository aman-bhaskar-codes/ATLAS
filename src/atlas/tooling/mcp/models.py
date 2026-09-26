"""MCP runtime models (Part 5 §6/§9/§29-§30/§82/§122).

`MCPServerDefinition` is the ATLAS-owned typed configuration — stdio and HTTP
transports are distinct validated variants. `ConnectionState` is an explicit
state machine: illegal transitions raise (§9). Credential REFERENCES only —
raw secrets never appear here (§7/§24/§27).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from atlas.tooling.mcp.errors import MCPConfigError


class TransportType(StrEnum):
    STDIO = "stdio"
    STREAMABLE_HTTP = "streamable_http"
    SSE_LEGACY = "sse_legacy"  # superseded; only for old servers (§10)


class StartupMode(StrEnum):
    LAZY = "lazy"  # registered but not started until needed (§63)
    EAGER = "eager"  # connect at startup, health+discovery before READY (§64)


class TrustLevel(StrEnum):
    """§29: explicit trust per server. Influences routing/policy metadata —
    NEVER a replacement for the SafetyEngine (§29/§94)."""

    LOCAL_USER_CONFIGURED = "local_user_configured"
    REMOTE_USER_CONFIGURED = "remote_user_configured"
    TRUSTED_REMOTE = "trusted_remote"
    UNTRUSTED = "untrusted"
    DISABLED = "disabled"


class ConnectionState(StrEnum):
    """§9: explicit connection lifecycle."""

    CONFIGURED = "CONFIGURED"
    VALIDATING = "VALIDATING"
    CONNECTING = "CONNECTING"
    AUTHENTICATING = "AUTHENTICATING"
    NEGOTIATING = "NEGOTIATING"
    READY = "READY"
    DEGRADED = "DEGRADED"
    RECONNECTING = "RECONNECTING"
    DISCONNECTING = "DISCONNECTING"
    DISCONNECTED = "DISCONNECTED"
    FAILED = "FAILED"
    DISABLED = "DISABLED"


LEGAL_CONNECTION_TRANSITIONS: dict[ConnectionState, frozenset[ConnectionState]] = {
    ConnectionState.CONFIGURED: frozenset(
        {ConnectionState.VALIDATING, ConnectionState.DISABLED, ConnectionState.FAILED}
    ),
    ConnectionState.VALIDATING: frozenset(
        {ConnectionState.CONNECTING, ConnectionState.FAILED, ConnectionState.DISABLED}
    ),
    ConnectionState.CONNECTING: frozenset(
        {
            ConnectionState.AUTHENTICATING,
            ConnectionState.NEGOTIATING,
            ConnectionState.FAILED,
            ConnectionState.DISCONNECTING,
        }
    ),
    ConnectionState.AUTHENTICATING: frozenset(
        {ConnectionState.NEGOTIATING, ConnectionState.FAILED, ConnectionState.DISCONNECTING}
    ),
    ConnectionState.NEGOTIATING: frozenset(
        {ConnectionState.READY, ConnectionState.FAILED, ConnectionState.DISCONNECTING}
    ),
    ConnectionState.READY: frozenset(
        {
            ConnectionState.DEGRADED,
            ConnectionState.RECONNECTING,
            ConnectionState.DISCONNECTING,
            ConnectionState.DISABLED,
        }
    ),
    ConnectionState.DEGRADED: frozenset(
        {
            ConnectionState.READY,
            ConnectionState.RECONNECTING,
            ConnectionState.DISCONNECTING,
            ConnectionState.FAILED,
        }
    ),
    ConnectionState.RECONNECTING: frozenset(
        {ConnectionState.CONNECTING, ConnectionState.FAILED, ConnectionState.DISCONNECTING}
    ),
    ConnectionState.DISCONNECTING: frozenset({ConnectionState.DISCONNECTED, ConnectionState.FAILED}),
    ConnectionState.DISCONNECTED: frozenset(
        {ConnectionState.CONNECTING, ConnectionState.DISABLED, ConnectionState.FAILED}
    ),
    ConnectionState.READY: frozenset(
        {
            ConnectionState.DEGRADED,
            ConnectionState.RECONNECTING,
            ConnectionState.DISCONNECTING,
            ConnectionState.DISABLED,
        }
    ),
    ConnectionState.FAILED: frozenset(
        {ConnectionState.CONNECTING, ConnectionState.DISABLED, ConnectionState.DISCONNECTED}
    ),
    ConnectionState.DISABLED: frozenset({ConnectionState.CONFIGURED}),
}


def transition_connection(current: ConnectionState, new: ConnectionState) -> ConnectionState:
    if new not in LEGAL_CONNECTION_TRANSITIONS[current]:
        from atlas.tooling.mcp.errors import MCPError

        raise MCPError(f"illegal MCP connection transition {current.value} -> {new.value}")
    return new


class AuthConfig(BaseModel):
    """Credential REFERENCES only (§24). ``mode: none|bearer|oauth``."""

    model_config = ConfigDict(frozen=True)

    mode: str = "none"  # none | bearer | oauth
    credential_ref: str | None = None  # vault reference, e.g. "mcp:remote_research"


class ReconnectPolicy(BaseModel):
    """§16/§67: bounded crash/reconnect protection."""

    model_config = ConfigDict(frozen=True)

    max_restarts: int = 3
    restart_window_s: float = 60.0
    initial_backoff_s: float = 1.0
    max_backoff_s: float = 30.0


class MCPServerDefinition(BaseModel):
    """§6: typed server configuration. stdio and HTTP variants are validated
    distinctly (§6). Loaded from config/mcp.yaml via the existing loader (§7)."""

    model_config = ConfigDict(frozen=True)

    server_id: str
    name: str = ""
    transport: TransportType
    enabled: bool = True
    startup: StartupMode = StartupMode.LAZY

    # stdio (§11-§13): structured subprocess parameters, never a shell string.
    command: str | None = None
    args: tuple[str, ...] = ()
    cwd: str | None = None
    env: dict[str, str] = Field(default_factory=dict)  # explicit allowlist values / resolved credentials

    # http (§17): url + endpoint policy.
    url: str | None = None
    endpoint_policy: str = "remote_public"  # remote_public | local_loopback | private_network | unrestricted

    auth: AuthConfig = Field(default_factory=AuthConfig)
    trust_level: TrustLevel = TrustLevel.LOCAL_USER_CONFIGURED

    timeout_s: float = 30.0  # connect/discovery timeout (§77: separate from tool-call timeout)
    tool_call_timeout_s: float = 120.0  # §18: tool calls stream longer than connect
    reconnect_policy: ReconnectPolicy = Field(default_factory=ReconnectPolicy)

    description: str = ""
    tags: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_transport_shape(self) -> MCPServerDefinition:
        if self.transport == TransportType.STDIO:
            if not self.command:
                raise MCPConfigError(f"stdio server {self.server_id!r} requires 'command'")
        else:
            if not self.url:
                raise MCPConfigError(
                    f"server {self.server_id!r} with transport {self.transport.value!r} requires 'url'"
                )
        return self

    @property
    def source_id(self) -> str:
        """§36: catalog source identity `mcp:<server_id>` — stable across reconnects."""
        return f"mcp:{self.server_id}"


class MCPServerInfo(BaseModel):
    """§30/§31/§82: negotiated connection facts, persisted once per server."""

    model_config = ConfigDict(frozen=True)

    server_id: str
    server_name: str = ""
    server_version: str = ""
    protocol_version: str = ""  # the NEGOTIATED version (§31) — never hard-coded
    capabilities: dict[str, Any] = Field(default_factory=dict)
    instructions: str = ""  # untrusted external metadata (§81) — never system-prompt material
    transport: TransportType = TransportType.STDIO
    connected_at: datetime | None = None


class MCPToolDescriptor(BaseModel):
    """Raw discovered tool before normalization (§32)."""

    model_config = ConfigDict(frozen=True)

    server_id: str
    name: str
    title: str = ""
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] | None = None
    annotations: dict[str, Any] = Field(default_factory=dict)  # §37: UNTRUSTED hints


class MCPResourceDescriptor(BaseModel):
    """§45: resources are cataloged separately from tools — never forced into
    the tool namespace (§83)."""

    model_config = ConfigDict(frozen=True)

    server_id: str
    uri: str
    name: str = ""
    description: str = ""
    mime_type: str = ""


class MCPPromptDescriptor(BaseModel):
    """§46: prompts are NOT executable tools (§83)."""

    model_config = ConfigDict(frozen=True)

    server_id: str
    name: str
    description: str = ""


__all__ = [
    "AuthConfig",
    "ConnectionState",
    "MCPPromptDescriptor",
    "MCPResourceDescriptor",
    "MCPServerDefinition",
    "MCPServerInfo",
    "MCPToolDescriptor",
    "ReconnectPolicy",
    "StartupMode",
    "TransportType",
    "TrustLevel",
    "transition_connection",
]
