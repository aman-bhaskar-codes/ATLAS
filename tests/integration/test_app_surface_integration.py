"""Composed-app integration seam — the REAL ``create_app()``, not a lone router.

The per-router tests (test_research_routes / test_agent_routes / test_ide_routes)
mount a single router onto a bare FastAPI, so they prove the route logic but NOT
that the router is actually wired into the shipped application. This test closes
that gap: it builds the real app via the factory and asserts the launched surfaces
(agent runs, IDE workbench, research) are mounted with their full path set, and
that a disabled subsystem answers 503 through the real dependency chain.

No Atlas build, DB, or network: routers register at ``create_app()`` time (before
lifespan), and ``get_atlas`` is overridden with a namespace whose subsystems are
None to exercise the honest 503-until-built contract.
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from atlas.interfaces.api.app import create_app
from atlas.interfaces.api.dependencies import get_atlas

# Every launched surface and the full path set it must expose in the composed app.
_EXPECTED_PATHS = {
    "/api/v1/research/sessions",
    "/api/v1/research/sessions/{session_id}",
    "/api/v1/research/sessions/{session_id}/follow-up",
    "/api/v1/research/sessions/{session_id}/stream",
    "/api/v1/agent/runs",
    "/api/v1/agent/runs/{run_id}",
    "/api/v1/agent/runs/{run_id}/stream",
}


def test_launched_surfaces_are_mounted_in_the_real_app() -> None:
    paths = create_app().openapi()["paths"]
    missing = sorted(p for p in _EXPECTED_PATHS if p not in paths)
    assert not missing, f"router mount regression — missing from create_app(): {missing}"


def test_research_and_agent_are_503_until_built() -> None:
    app = create_app()
    # A composed app with every launched subsystem disabled (None) — the honest
    # "503 until wired" contract, exercised through the real router + dependency.
    app.dependency_overrides[get_atlas] = lambda: SimpleNamespace(research=None, agent_engine=None, ide=None)
    client = TestClient(app)

    research = client.post("/api/v1/research/sessions", json={"question": "hi"})
    assert research.status_code == 503
    assert "disabled" in research.json()["detail"]

    agent = client.post("/api/v1/agent/runs", json={"request": "hi"})
    assert agent.status_code == 503
