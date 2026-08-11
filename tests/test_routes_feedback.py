"""Regression tests for feedback API dependency injection."""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from atlas.interfaces.api.dependencies import get_atlas
from atlas.interfaces.api.routes_feedback import router


class FakeFeedbackStore:
    async def record(
        self,
        *,
        task_id: str,
        rating: int,
        comment: str | None = None,
        original_output: str | None = None,
        edited_output: str | None = None,
    ) -> str:
        return "feedback-1"


def test_submit_feedback_uses_injected_atlas() -> None:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_atlas] = lambda: SimpleNamespace(feedback=FakeFeedbackStore())

    response = TestClient(app).post(
        "/feedback",
        json={"task_id": "task-1", "rating": 1},
    )

    assert response.status_code == 200
    assert response.json() == {"id": "feedback-1", "status": "recorded"}
