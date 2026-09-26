"""MCP runtime protocol + lifecycle tests over REAL stdio transport (Part 5
§101-§108). The official SDK runs a real child process; no public servers."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from atlas.tooling.mcp.config import load_mcp_server_definitions
from atlas.tooling.mcp.errors import MCPConfigError, MCPSecurityError
from atlas.tooling.mcp.manager import MCPServerManager
from atlas.tooling.mcp.models import MCPServerDefinition, TransportType
from atlas.tooling.mcp.security import EndpointPolicy, EnvironmentPolicy, StdioCommandPolicy
from atlas.tooling.registry.registry import ToolingRegistry

FIXTURES = Path(__file__).parent / "mcp_fixtures"
PYTHON = sys.executable


def _stdio_definition(server_id: str = "echo", script: str = "echo_server.py", **overrides: Any) -> MCPServerDefinition:
    defaults: dict[str, Any] = {
        "server_id": server_id,
        "transport": TransportType.STDIO,
        "enabled": True,
        "command": PYTHON,
        "args": (str(FIXTURES / script),),
        "trust_level": "local_user_configured",
        "timeout_s": 20.0,
    }
    defaults.update(overrides)
    return MCPServerDefinition(**defaults)


@pytest.fixture
def registry() -> ToolingRegistry:
    return ToolingRegistry()


# ── Configuration (§6-§8) ──────────────────────────────────────────────── #


def test_definition_validation_distinguishes_transports() -> None:
    """§6: stdio requires command; HTTP requires url."""
    with pytest.raises(MCPConfigError, match="command"):
        MCPServerDefinition(server_id="bad", transport=TransportType.STDIO)
    with pytest.raises(MCPConfigError, match="url"):
        MCPServerDefinition(server_id="bad", transport=TransportType.STREAMABLE_HTTP)


def test_config_loader_reads_mcp_yaml(tmp_path: Any) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "mcp.yaml").write_text(
        """
mcp:
  servers:
    github:
      enabled: true
      transport: stdio
      command: npx
      args: ["-y", "@modelcontextprotocol/server-github"]
      env:
        GITHUB_PERSONAL_ACCESS_TOKEN:
          credential_ref: github:default
      trust_level: remote_user_configured
"""
    )
    definitions = load_mcp_server_definitions(config_dir)
    assert len(definitions) == 1
    d = definitions[0]
    assert d.server_id == "github" and d.enabled is True
    assert d.env["GITHUB_PERSONAL_ACCESS_TOKEN"] == "github:default"  # a REFERENCE (§24)
    assert d.trust_level.value == "remote_user_configured"


def test_missing_config_yields_no_servers(tmp_path: Any) -> None:
    assert load_mcp_server_definitions(tmp_path) == []


# ── Security policies (§11-§13/§20-§21/§115) ───────────────────────────── #


def test_stdio_command_policy_blocks_shell_metacharacters() -> None:
    """§12/§115: shell metacharacters never become execution semantics."""
    policy = StdioCommandPolicy()
    with pytest.raises(MCPSecurityError):
        policy.validate(command="npx -y evil; rm -rf /", args=(), cwd=None)
    # args are passed as argv WITHOUT a shell (§11): `$(...)` stays literal
    # data and cannot become execution semantics — validated, not transformed.
    policy.validate(command="npx", args=("$(rm -rf /)",), cwd=None)
    policy.validate(command="npx", args=("-y", "@modelcontextprotocol/server-github"), cwd=None)  # ok


def test_stdio_command_policy_enforces_allowlist_and_caps() -> None:
    policy = StdioCommandPolicy(allowed_executables=("npx", "uvx"))
    with pytest.raises(MCPSecurityError, match="allowed-executable"):
        policy.validate(command="/bin/evil", args=(), cwd=None)
    with pytest.raises(MCPSecurityError, match="exceed"):
        policy.validate(command="npx", args=tuple(f"a{i}" for i in range(64)), cwd=None)


def test_environment_policy_is_allowlisted() -> None:
    """§13/§115: ATLAS's full environment is never passed through."""
    import os

    env = EnvironmentPolicy().build({"MY_TOKEN": "resolved-secret"})
    assert env["MY_TOKEN"] == "resolved-secret"
    assert set(env) <= {"PATH", "HOME", "LANG", "TMPDIR", "SHELL", "MY_TOKEN"}
    assert "MY_TOKEN" not in os.environ or env.get("MY_TOKEN") != os.environ.get("DEFINITELY_NOT_SET_XYZ")


def test_endpoint_policy_blocks_private_and_metadata_targets() -> None:
    """§20/§115: SSRF guard."""
    public = EndpointPolicy("remote_public")
    public.validate_url("https://mcp.example.com/mcp")  # ok
    with pytest.raises(MCPSecurityError):
        public.validate_url("http://169.254.169.254/latest/meta-data")  # metadata
    with pytest.raises(MCPSecurityError):
        public.validate_url("http://10.0.0.5/mcp")  # private
    with pytest.raises(MCPSecurityError):
        public.validate_url("file:///etc/passwd")  # scheme

    loopback = EndpointPolicy("local_loopback")
    loopback.validate_url("http://127.0.0.1:8080/mcp")  # local servers are legitimate (§20)


# ── Real stdio transport: connect → negotiate → discover → call ───────── #


@pytest.mark.asyncio
async def test_stdio_connect_negotiate_discover_call(registry: ToolingRegistry) -> None:
    """§103/§104/§139: the REAL stdio path through the official SDK."""
    manager = MCPServerManager(
        definitions=[_stdio_definition()],
        tooling_registry=registry,
    )
    definitions = await manager.refresh("echo")

    assert len(definitions) == 3
    ids = {d.id for d in definitions}
    assert "mcp:echo:echo" in ids
    assert all(d.namespace.value == "mcp" for d in definitions)
    echo_def = next(d for d in definitions if d.id == "mcp:echo:echo")
    assert echo_def.provider == "echo"
    assert echo_def.safety_tool == "mcp"
    # §38: exact input schema preserved per operation
    schema = echo_def.input_schema.schema_for("call")
    assert schema["type"] == "object"

    status = manager.status("echo")
    assert status["state"] == "READY"
    assert status["protocol_version"]  # §31: negotiated, persisted
    assert status["server_name"] == "atlas-echo"

    # §53/§56: tools/call + normalization with provenance
    call = await manager.call_tool("echo", "echo", {"text": "hello"})
    assert call["ok"] is True
    # the SDK's structured content wraps scalar returns: {"result": ...} (§56)
    assert call["data"] == {"result": "echo:hello"}
    provenance = call["metadata"]["provenance"]
    assert provenance["source_kind"] == "mcp" and provenance["server_id"] == "echo"
    assert provenance["protocol_version"] == status["protocol_version"]  # §59

    await manager.shutdown()


@pytest.mark.asyncio
async def test_structured_and_error_results(registry: ToolingRegistry) -> None:
    """§56/§113: structured content and is_error preserve semantics."""
    manager = MCPServerManager(definitions=[_stdio_definition()], tooling_registry=registry)
    await manager.refresh("echo")

    add = await manager.call_tool("echo", "add", {"a": 2, "b": 3})
    assert add["ok"] is True
    # the server returned a dict without an output schema, so the SDK delivers
    # it as JSON text content — preserved as data (§56)
    assert '"sum"' in str(add["data"]) and "5" in str(add["data"])

    failed = await manager.call_tool("echo", "always_fails", {})
    assert failed["ok"] is False  # is_error preserved (§56)
    # the server wraps the tool error text; is_error + a message are preserved (§56)
    assert failed["error"]["message"]  # non-empty error message

    await manager.shutdown()


@pytest.mark.asyncio
async def test_many_tools_discovered(registry: ToolingRegistry) -> None:
    """§104/§107: many tools discovered, no duplicates, stable ids."""
    manager = MCPServerManager(
        definitions=[_stdio_definition("paginated", "paginated_server.py")],
        tooling_registry=registry,
    )
    definitions = await manager.refresh("paginated")
    assert len(definitions) == 25
    assert len({d.id for d in definitions}) == 25  # no duplicates (§107)
    assert all(d.id.startswith("mcp:paginated:tool_") for d in definitions)
    await manager.shutdown()


# ── Catalog/registry integration (§41/§73/§74/§84) ────────────────────── #


@pytest.mark.asyncio
async def test_tools_registered_into_tooling_registry(registry: ToolingRegistry) -> None:
    """§41/§73: MCP tools enter the universal registry — router sees them like
    every other tool, no special-casing."""
    manager = MCPServerManager(definitions=[_stdio_definition()], tooling_registry=registry)
    await manager.refresh("echo")
    definitions = registry.list_definitions()
    assert any(d.id == "mcp:echo:echo" for d in definitions)

    # §84: a second server exposing the SAME tool name gets a distinct id.
    second = ToolingRegistry()
    manager2 = MCPServerManager(definitions=[_stdio_definition("other", "echo_server.py")], tooling_registry=second)
    await manager2.refresh("other")
    second_ids = {d.id for d in second.list_definitions()}
    assert "mcp:other:echo" in second_ids  # namespace-disambiguated (§84/§85)
    assert registry.get("mcp:echo:echo") is not None  # first server untouched
    await manager.shutdown()
    await manager2.shutdown()


@pytest.mark.asyncio
async def test_disable_prevents_execution(registry: ToolingRegistry) -> None:
    """§125/§136: a disabled server is never routed/executed."""
    from atlas.tooling.mcp.errors import MCPDisabledError

    manager = MCPServerManager(definitions=[_stdio_definition()], tooling_registry=registry)
    await manager.refresh("echo")
    manager.set_enabled("echo", False)
    with pytest.raises(MCPDisabledError):
        await manager.call_tool("echo", "echo", {"text": "x"})
    await manager.shutdown()
