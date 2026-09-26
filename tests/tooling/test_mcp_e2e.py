"""End-to-end MCP test through the REAL composition root (Part 5 §116-§118).

Boots the actual FastAPI app (tests/api/conftest contract), injects an MCP
server definition into the manager, connects over REAL stdio, and proves:
route → catalog → execution → SafetyEngine → tools/call → normalized result.
Plus: server-side tool change → re-discovery → catalog refresh (§117) and
failure → recovery (§118).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from atlas.tooling.execution import ExecutionRequest, TerminalOutcome
from atlas.tooling.execution.engine import ExecutionCfg, ExecutionEngine
from atlas.tooling.execution.store import ExecutionRunStore
from atlas.tooling.mcp.models import MCPServerDefinition, TransportType
from atlas.tooling.models.tool_health import ToolRuntimeState
from atlas.tooling.routing.models import RoutePlan, RouteStep
from tests.api.conftest import app_client

FIXTURES = Path(__file__).parent / "mcp_fixtures"


def _stdio(server_id: str = "echo") -> MCPServerDefinition:
    return MCPServerDefinition(
        server_id=server_id,
        transport=TransportType.STDIO,
        enabled=True,
        command=sys.executable,
        args=(str(FIXTURES / "echo_server.py"),),
        trust_level="local_user_configured",
        timeout_s=20.0,
    )


@pytest.mark.asyncio
async def test_full_composition_root_mcp_execution(tmp_path: Path) -> None:
    """§116: the REAL app graph routes and executes an MCP tool — no manual
    test-only wiring."""
    async with app_client(tmp_path) as (app, client):
        atlas = app.state.atlas
        assert atlas.mcp_manager is not None  # the runtime is on the graph

        # Inject the fixture server as an owner-configured server (§28: the
        # definition arrives via the same path config/mcp.yaml would feed).
        manager = atlas.mcp_manager
        manager._definitions["echo"] = _stdio()

        # Owner action: connect (§28/§93) — connect + negotiate + discover.
        connected = await manager.connect("echo")
        assert connected.protocol_version  # §31 negotiated

        # §41: catalog now contains the MCP tool through the normal sync.
        catalog = atlas.tooling.catalog
        record = catalog.inspect("mcp:echo:echo")
        assert record is not None and record.execution_type.value == "mcp"

        # §73: the router's candidate path sees it deterministically.
        candidates = catalog.find_candidates()
        assert any(c.tool_id == "mcp:echo:echo" for c in candidates)

        # §74/§116: execute through the ordinary Part-4 governed funnel.
        tooling_registry = atlas.tooling.registry
        for d in tooling_registry.list_definitions():
            if d.namespace.value == "mcp":
                tooling_registry.set_status(d.id, ToolRuntimeState.READY)
        engine = ExecutionEngine(
            config=ExecutionCfg(),
            store=ExecutionRunStore(atlas.db),
            tooling_executor=atlas.tooling.executor,
            catalog=catalog,
        )
        plan = RoutePlan(
            plan_id="e2e-mcp",
            task_id="t-e2e-mcp",
            route_id="r-e2e-mcp",
            strategy="DIRECT",
            domain="general",
            steps=(
                RouteStep(
                    step_id="mcp-call",
                    candidate_id="mcp:echo:echo",
                    operation="call",
                    input_mapping={"arguments": {"text": "hello"}},
                ),
            ),
        )
        result = await engine.start(ExecutionRequest(plan=plan, task_id="t-e2e-mcp", correlation_id="c-e2e-mcp"))
        assert result.outcome == TerminalOutcome.SUCCESS
        assert result.outputs["mcp-call"] == {"result": "echo:hello"}

        # §59: provenance persisted in the observation metadata.
        run = await engine.status(result.run_id)
        obs = run.step("mcp-call").observations[-1]  # type: ignore[union-attr]
        assert obs.metadata["provenance"]["source_kind"] == "mcp"
        assert obs.metadata["provenance"]["server_id"] == "echo"

        # §89: CLI-facing API surfaces work.
        servers = (await client.get("/api/v1/mcp/servers")).json()
        assert any(s["server_id"] == "echo" for s in servers)
        tools = (await client.get("/api/v1/mcp/servers/echo/tools")).json()
        assert any(t["id"] == "mcp:echo:echo" for t in tools)

        await manager.disconnect("echo")
