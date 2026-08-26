from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from atlas.interfaces.api.routes_learning import router as learning_router


@pytest.fixture
def mock_atlas():
    atlas = SimpleNamespace()

    # Mock Consolidator
    atlas.consolidator = MagicMock()
    atlas.consolidator.run = AsyncMock(return_value={"episodes": 10, "applied": 5, "proposed": 2})

    # Mock SkillPromoter
    atlas.skill_promoter = MagicMock()
    mock_skill1 = MagicMock()
    mock_skill1.name = "research: browser"
    mock_skill2 = MagicMock()
    mock_skill2.name = "coding: python"
    atlas.skill_promoter.promote_from_experiences = AsyncMock(return_value=[mock_skill1, mock_skill2])

    return atlas


@pytest.fixture
def test_client(mock_atlas) -> TestClient:
    app = FastAPI()
    app.include_router(learning_router, prefix="/api/v1")
    app.state.atlas = mock_atlas
    return TestClient(app)


def test_consolidation_endpoint(test_client: TestClient, mock_atlas: SimpleNamespace) -> None:
    response = test_client.post("/api/v1/learning/consolidate")
    assert response.status_code == 200
    data = response.json()
    assert data["episodes"] == 10
    assert data["applied"] == 5
    assert data["proposed"] == 2
    mock_atlas.consolidator.run.assert_called_once()


def test_promotion_endpoint(test_client: TestClient, mock_atlas: SimpleNamespace) -> None:
    response = test_client.post("/api/v1/learning/promote?limit=15")
    assert response.status_code == 200
    data = response.json()
    assert data["promoted_skills"] == 2
    assert data["skill_names"] == ["research: browser", "coding: python"]
    mock_atlas.skill_promoter.promote_from_experiences.assert_called_once_with(limit=15)
