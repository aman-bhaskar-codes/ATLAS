from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from tests.capabilities.ide.test_training_pipeline import jsonl, trace
from tests.capabilities.ide.test_training_service import FakeIDE

from atlas.capabilities.ide.training.service import TrainingService
from atlas.interfaces.api.routes_training import router


def app_for(root: Path) -> FastAPI:
    app = FastAPI()
    app.state.api_keys = {"owner": "admin", "viewer": "readonly"}
    app.state.atlas = SimpleNamespace(training_service=TrainingService(ide=FakeIDE(root)))
    app.include_router(router)
    return app


@pytest.mark.parametrize(
    "endpoint,body",
    [
        ("/datasets/validate", {"path": "traces.jsonl"}),
        ("/datasets/prepare", {"path": "traces.jsonl", "approved": True}),
        ("/verification", {"command": "pytest -q"}),
        ("/evaluations", {"export_id": "e", "baseline_path": "base", "candidate_path": "candidate"}),
        ("/jobs", {"export_id": "e", "base_path": "model", "approved": True}),
    ],
)
async def test_readonly_cannot_mutate_training_or_export_data(
    tmp_path: Path, endpoint: str, body: dict[str, object]
) -> None:
    app = app_for(tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/ide/training" + endpoint,
            json={"workspace_id": "workspace", **body},
            headers={"Authorization": "Bearer viewer"},
        )
    assert response.status_code == 403
    assert not (tmp_path / ".atlas-training").exists()


async def test_api_preparation_is_not_training_and_missing_compute_returns_503(tmp_path: Path) -> None:
    app = app_for(tmp_path)
    (tmp_path / "traces.jsonl").write_text(jsonl([trace(f"task-{i}") for i in range(5)]))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"Authorization": "Bearer owner"}
    ) as client:
        refused = await client.post(
            "/api/v1/ide/training/datasets/prepare",
            json={
                "workspace_id": "workspace",
                "path": "traces.jsonl",
                "approved": False,
            },
        )
        assert refused.status_code == 422
        assert not (tmp_path / ".atlas-training").exists()
        exported = await client.post(
            "/api/v1/ide/training/datasets/prepare",
            json={
                "workspace_id": "workspace",
                "path": "traces.jsonl",
                "approved": True,
            },
        )
        assert exported.status_code == 200
        assert exported.json()["training_completed"] is False
        readiness = await client.get("/api/v1/ide/training/readiness", params={"workspace_id": "workspace"})
        assert readiness.status_code == 200
        assert readiness.json()["ready"] is False
        started = await client.post(
            "/api/v1/ide/training/jobs",
            json={
                "workspace_id": "workspace",
                "export_id": exported.json()["export_id"],
                "base_path": "model",
                "approved": True,
            },
        )
        assert started.status_code == 503
        assert app.state.atlas.training_service._ide.commands == []


async def test_cli_validates_and_prepares_only_explicit_local_synthetic_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import json

    from atlas.capabilities.ide.training.cli import main

    (tmp_path / "traces.jsonl").write_text(jsonl([trace(f"task-{i}") for i in range(5)]))
    monkeypatch.setattr("sys.argv", ["training", "--workspace", str(tmp_path), "validate", "traces.jsonl"])
    main()
    assert json.loads(capsys.readouterr().out)["examples"] == 5
    monkeypatch.setattr(
        "sys.argv", ["training", "--workspace", str(tmp_path), "prepare", "traces.jsonl", "--approve-local-export"]
    )
    main()
    assert json.loads(capsys.readouterr().out)["training_completed"] is False


async def test_runtime_evaluation_requires_admin_and_reports_missing_runtime(tmp_path: Path) -> None:
    app = app_for(tmp_path)
    body = {
        "workspace_id": "workspace",
        "export_id": "e",
        "baseline_model": "local",
        "candidate_model": "candidate",
        "adapter_path": "adapter",
        "approved": True,
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        denied = await client.post(
            "/api/v1/ide/training/evaluations/runtime", json=body, headers={"Authorization": "Bearer viewer"}
        )
        assert denied.status_code == 403
        unavailable = await client.post(
            "/api/v1/ide/training/evaluations/runtime", json=body, headers={"Authorization": "Bearer owner"}
        )
        assert unavailable.status_code == 503
