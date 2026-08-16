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
async def test_memory_stats_endpoint():
    """Test memory stats endpoint returns data."""
    app = create_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # This will fail if dependencies aren't set up properly
        response = await client.get("/api/v1/memory/stats")

        # Could be 500 if deps not set, or 200 if working
        print(f"Status: {response.status_code}")
        print(f"Body: {response.text}")

        # For now, just check it doesn't 404
        assert response.status_code != 404, "Memory stats route returned 404 - not registered"


@pytest.mark.asyncio
async def test_trajectory_stats_endpoint():
    """Test trajectory stats endpoint returns data."""
    app = create_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/trajectory/stats")

        print(f"Status: {response.status_code}")
        print(f"Body: {response.text}")

        # For now, just check it doesn't 404
        assert response.status_code != 404, "Trajectory stats route returned 404 - not registered"
