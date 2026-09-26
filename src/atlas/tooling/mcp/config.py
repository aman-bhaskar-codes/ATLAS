"""MCP configuration loading (Part 5 §7-§8).

Uses the EXISTING ATLAS config loader pattern (yaml via infra config
conventions) — no second loader. `config/mcp.yaml` carries credential
REFERENCES only (§8/§27): env entries may be plain strings or
``{credential_ref: ...}`` mappings resolved from the identity vault at
connect time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from atlas.tooling.mcp.errors import MCPConfigError
from atlas.tooling.mcp.models import (
    AuthConfig,
    MCPServerDefinition,
    ReconnectPolicy,
    StartupMode,
    TransportType,
    TrustLevel,
)


def load_mcp_server_definitions(config_dir: Path) -> list[MCPServerDefinition]:
    """Load server definitions from ``config/mcp.yaml``. A missing file means
    no servers — MCP is an opt-in, owner-configured capability (§28/§93)."""
    path = config_dir / "mcp.yaml"
    if not path.exists():
        return []
    try:
        raw: dict[str, Any] = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise MCPConfigError(f"invalid YAML in {path}: {exc}") from exc
    servers = raw.get("mcp", {}).get("servers", {})
    if not isinstance(servers, dict):
        raise MCPConfigError(f"{path}: 'mcp.servers' must be a mapping")
    return [_definition_from_yaml(str(server_id), spec) for server_id, spec in servers.items()]


def _definition_from_yaml(server_id: str, spec: dict[str, Any]) -> MCPServerDefinition:
    env = {
        str(k): str(v.get("credential_ref", "") if isinstance(v, dict) else v)
        for k, v in (spec.get("env") or {}).items()
    }
    reconnect = spec.get("reconnect") or {}
    auth = spec.get("auth") or {}
    command = spec.get("command")
    if isinstance(command, list):  # list-form command: head is the executable
        command = str(command[0]) if command else None
        extra_args = tuple(str(a) for a in (spec.get("command") or [])[1:])
    else:
        extra_args = ()
    return MCPServerDefinition(
        server_id=server_id,
        name=str(spec.get("name", server_id)),
        transport=TransportType(spec.get("transport", "stdio")),
        enabled=bool(spec.get("enabled", False)),
        startup=StartupMode(spec.get("startup", "lazy")),
        command=command if isinstance(command, str) else None,
        args=tuple(str(a) for a in (spec.get("args") or ())) + extra_args,
        cwd=spec.get("cwd"),
        env=env,
        url=spec.get("url"),
        endpoint_policy=str(spec.get("endpoint_policy", "remote_public")),
        auth=AuthConfig(
            mode=str(auth.get("mode", "none")),
            credential_ref=auth.get("credential_ref"),
        ),
        trust_level=TrustLevel(spec.get("trust_level", "local_user_configured")),
        timeout_s=float(spec.get("timeout_s", 30.0)),
        tool_call_timeout_s=float(spec.get("tool_call_timeout_s", 120.0)),
        reconnect_policy=ReconnectPolicy(
            max_restarts=int(reconnect.get("max_restarts", 3)),
            restart_window_s=float(reconnect.get("restart_window_s", 60.0)),
            initial_backoff_s=float(reconnect.get("initial_backoff_s", 1.0)),
            max_backoff_s=float(reconnect.get("max_backoff_s", 30.0)),
        ),
        description=str(spec.get("description", "")),
        tags=tuple(str(t) for t in (spec.get("tags") or ())),
    )


__all__ = ["load_mcp_server_definitions"]
