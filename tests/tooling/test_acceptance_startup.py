"""Application startup acceptance test (§75/§76) — the REAL composition root
must construct the tooling fabric, and the real CLI backend endpoint must
serve it.

This boots the actual FastAPI app through its real lifespan (Atlas built and
started exactly as in production, externals mocked per the tests/api/conftest
contract), then asserts the fabric exists on the Atlas graph, that the real
native tools and capabilities are registered, and that GET /api/v1/tools —
the endpoint `atlas tools list` consumes — returns them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from atlas.tooling.models.tool_health import ToolRuntimeState
from tests.api.conftest import app_client


@pytest.mark.asyncio
async def test_real_startup_builds_the_tooling_fabric(tmp_path: Path) -> None:
    async with app_client(tmp_path) as (app, client):
        atlas = app.state.atlas

        # §63: the fabric is reachable on the real composition graph.
        assert atlas.tooling is not None
        registry = atlas.tooling.registry
        definitions = {d.id: d for d in registry.list_definitions()}

        # Native tools bridged from the real ToolRegistry registrations.
        for expected in ("native:atlas:filesystem", "native:atlas:shell"):
            assert expected in definitions, f"missing {expected}; registered: {sorted(definitions)}"
            assert definitions[expected].namespace.value == "native"
            assert definitions[expected].execution_type.value == "native"
            assert definitions[expected].safety_tool == definitions[expected].name

        # Capabilities bridged from the real CapabilityRegistry registrations.
        for expected in (
            "capability:atlas:knowledge",
            "capability:atlas:email",
            "capability:atlas:calendar",
            "capability:atlas:weather",
        ):
            assert expected in definitions, f"missing {expected}; registered: {sorted(definitions)}"

        # No speculative registration: only real tools/capabilities exist.
        assert "capability:atlas:github" not in definitions  # enum member, no spec registered
        assert "mcp:github:search_repositories" not in definitions  # Part 4+

        # Lifecycle ran: adapters initialized and validated.
        for registration in registry.list_registrations():
            assert registration.status.state in {
                ToolRuntimeState.READY,
                ToolRuntimeState.REGISTERED,
            }, f"{registration.definition.id}: {registration.status}"

        # The knowledge tool (native) and knowledge capability are distinct,
        # honestly named entries — the native tool and the dispatcher path.
        assert "native:atlas:knowledge" in definitions
        assert definitions["native:atlas:knowledge"].capability is None

        # §76: the CLI's backend endpoint lists the same real registry.
        response = await client.get("/api/v1/tools")
        assert response.status_code == 200
        body = response.json()
        served_ids = {tool["id"] for tool in body}
        assert served_ids == set(definitions)
        filesystem = next(t for t in body if t["id"] == "native:atlas:filesystem")
        assert filesystem["status"] == "ready"
        assert "read" in filesystem["operations"]
        assert filesystem["default_tier_name"] in {"AUTO", "NOTIFY", "CONFIRM", "DANGEROUS"}


@pytest.mark.asyncio
async def test_real_startup_descriptor_tiers_come_from_the_real_classifier(tmp_path: Path) -> None:
    """The descriptor tier is computed by the REAL TierClassifier against the
    REAL permissions manifest at build time (documented policy, not invention)."""
    from atlas.infra.types import Tier

    async with app_client(tmp_path) as (app, _client):
        registry = app.state.atlas.tooling.registry
        shell = registry.require("native:atlas:shell")
        # The manifest tiers shell.side_effect at CONFIRM; the descriptor must
        # reflect that (max across operations), not a hardcoded AUTO.
        assert shell.definition.policy.default_tier >= Tier.CONFIRM
        filesystem = registry.require("native:atlas:filesystem")
        assert filesystem.definition.policy.default_tier >= Tier.NOTIFY

        # sanity: the manifest was actually loaded in this process
        assert app.state.atlas.manifest is not None
