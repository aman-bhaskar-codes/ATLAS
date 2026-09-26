"""Routing API acceptance over the REAL composition root (Part 3 §69/§92).

Boots the actual FastAPI app (externals mocked per tests/api/conftest), then
routes a real request through POST /api/v1/routing/route and inspects it —
proving the fabric is wired into the running application graph.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.api.conftest import app_client


@pytest.mark.asyncio
async def test_real_application_routes_a_request(tmp_path: Path) -> None:
    async with app_client(tmp_path) as (app, client):
        atlas = app.state.atlas
        assert atlas.routing is not None  # §92: fabric is on the real graph

        # §69: domain/strategy registries are visible.
        domains = (await client.get("/api/v1/routing/domains")).json()
        assert {d["id"] for d in domains} >= {"general", "research", "agentic_ide"}
        strategies = (await client.get("/api/v1/routing/strategies")).json()
        assert any(s["strategy_id"] == "DIRECT" for s in strategies)

        # §92: a real request produces a real, inspectable routing decision.
        routed = await client.post("/api/v1/routing/route", json={"request": "read the readme file"})
        assert routed.status_code == 200
        body = routed.json()
        decision = body["decision"]
        assert decision["decision_type"] == "route"
        assert decision["selected_candidate"] is not None
        assert body["plan"] is not None  # a compiled route plan accompanies the decision

        # §68: structured explanation from the persisted record.
        explain = await client.get(f"/api/v1/routing/routes/{decision['route_id']}/explain")
        assert explain.status_code == 200
        assert len(explain.json()["reasons"]) >= 4

        # §67: replay reconstructs from the recorded snapshot.
        replay = await client.post(f"/api/v1/routing/routes/{decision['route_id']}/replay")
        assert replay.status_code == 200
        assert replay.json()["decision"]["selected_candidate"] == decision["selected_candidate"]
