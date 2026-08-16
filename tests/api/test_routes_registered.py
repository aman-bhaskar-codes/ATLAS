"""Test that all API routes are properly registered and accessible."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from atlas.interfaces.api.app import create_app


@pytest.mark.asyncio
async def test_all_routes_registered():
    """Verify all route modules have their endpoints registered in OpenAPI spec."""
    app = create_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/openapi.json")
        assert response.status_code == 200

        spec = response.json()
        paths = spec.get("paths", {})

        # Runtime routes
        assert "/api/v1/runtime/status" in paths, "Runtime routes missing"
        assert "/api/v1/runtime/health" in paths

        # Task routes
        assert "/api/v1/tasks" in paths, "Task routes missing"

        # Memory routes (Phase 3)
        assert "/api/v1/memory/stats" in paths, "Memory stats route missing"
        assert "/api/v1/memory/episodes" in paths, "Memory episodes route missing"
        assert "/api/v1/memory/facts" in paths, "Memory facts route missing"
        assert "/api/v1/memory/knowledge" in paths, "Memory knowledge route missing"
        assert "/api/v1/memory/preferences" in paths, "Memory preferences route missing"

        # Trajectory routes (Phase 2)
        assert "/api/v1/trajectory/recent" in paths, "Trajectory recent route missing"
        assert "/api/v1/trajectory/stats" in paths, "Trajectory stats route missing"
        assert "/api/v1/trajectory/experiences" in paths, "Trajectory experiences route missing"
        assert "/api/v1/trajectory/failures" in paths, "Trajectory failures route missing"


@pytest.mark.asyncio
async def test_learning_and_ops_routes_registered():
    """Batch 6 routes are registered (behavior covered by tests/interfaces)."""
    app = create_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/openapi.json")
        assert response.status_code == 200
        paths = response.json().get("paths", {})

        for path in (
            "/api/v1/learning/skills",
            "/api/v1/learning/strategies",
            "/api/v1/learning/world",
            "/api/v1/learning/analytics",
            "/api/v1/ops/tools",
            "/api/v1/ops/models",
            "/api/v1/ops/providers",
            "/api/v1/ops/schedules",
        ):
            assert path in paths, f"{path} missing"
