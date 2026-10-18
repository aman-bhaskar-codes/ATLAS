"""IDE training projection. Local preparation is distinct from approved compute."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from atlas.capabilities.ide.training.datasets import DatasetError
from atlas.capabilities.ide.training.service import TrainingNotReadyError, TrainingService
from atlas.interfaces.api.auth import require_admin

router = APIRouter(prefix="/api/v1/ide/training", tags=["ide-training"])


def _service(request: Request) -> TrainingService:
    service = getattr(request.app.state.atlas, "training_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="IDE training workflow is not available")
    return service  # type: ignore[no-any-return]


async def _call[T](action: Callable[[], Awaitable[T]]) -> T:
    try:
        return await action()
    except TrainingNotReadyError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except DatasetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (ValueError, KeyError, TypeError):
        raise HTTPException(status_code=422, detail="training input or manifest is invalid") from None


class DatasetRequest(BaseModel):
    model_config = {"extra": "forbid"}
    workspace_id: str
    path: str
    approved: bool = False


class VerifyRequest(BaseModel):
    model_config = {"extra": "forbid"}
    workspace_id: str
    command: str = Field(min_length=1, max_length=2000)


class ExportRunsRequest(BaseModel):
    model_config = {"extra": "forbid"}
    workspace_id: str
    run_ids: list[str] = Field(min_length=5, max_length=500)
    verification_id: str
    approved: bool = False


class EvaluationRequest(BaseModel):
    model_config = {"extra": "forbid"}
    workspace_id: str
    export_id: str
    baseline_path: str
    candidate_path: str
    adapter_path: str = ""


class PromotionRequest(BaseModel):
    model_config = {"extra": "forbid"}
    workspace_id: str
    evaluation_id: str
    adapter_path: str
    approved: bool = False


class StartTrainingRequest(BaseModel):
    model_config = {"extra": "forbid"}
    workspace_id: str
    export_id: str
    base_path: str
    approved: bool = False
    iterations: int = Field(default=100, ge=1, le=500)
    timeout_s: float = Field(default=900, ge=1, le=1800)


@router.get("/readiness")
async def readiness(request: Request, workspace_id: str, base_path: str = "") -> Any:
    service = _service(request)
    return await _call(lambda: service.readiness(workspace_id, base_path))


@router.post("/datasets/validate", dependencies=[Depends(require_admin)])
async def validate_dataset(body: DatasetRequest, request: Request) -> Any:
    service = _service(request)
    return await _call(lambda: service.validate(body.workspace_id, body.path))


@router.post("/datasets/prepare", dependencies=[Depends(require_admin)])
async def prepare_dataset(body: DatasetRequest, request: Request) -> Any:
    service = _service(request)
    return await _call(lambda: service.prepare(body.workspace_id, body.path, approved=body.approved))


@router.post("/verification", dependencies=[Depends(require_admin)])
async def verify_workspace(body: VerifyRequest, request: Request) -> Any:
    service = _service(request)
    return await _call(lambda: service.verify(body.workspace_id, body.command))


@router.post("/datasets/export-runs", dependencies=[Depends(require_admin)])
async def export_runs(body: ExportRunsRequest, request: Request) -> Any:
    service = _service(request)
    return await _call(
        lambda: service.export_runs(
            body.workspace_id,
            body.run_ids,
            body.verification_id,
            approved=body.approved,
        )
    )


@router.post("/evaluations", dependencies=[Depends(require_admin)])
async def evaluate(body: EvaluationRequest, request: Request) -> Any:
    service = _service(request)
    return await _call(
        lambda: service.evaluate(
            body.workspace_id,
            body.export_id,
            body.baseline_path,
            body.candidate_path,
            body.adapter_path,
        )
    )


@router.post("/promotions", dependencies=[Depends(require_admin)])
async def promote(body: PromotionRequest, request: Request) -> Any:
    service = _service(request)
    return await _call(
        lambda: service.promote(body.workspace_id, body.evaluation_id, body.adapter_path, approved=body.approved)
    )


@router.get("/jobs")
async def list_jobs(request: Request, workspace_id: str) -> Any:
    service = _service(request)
    return await _call(lambda: service.jobs(workspace_id))


@router.get("/datasets")
async def list_datasets(request: Request, workspace_id: str) -> Any:
    service = _service(request)
    return await _call(lambda: service.exports(workspace_id))


@router.post("/jobs", dependencies=[Depends(require_admin)])
async def start_job(body: StartTrainingRequest, request: Request) -> Any:
    service = _service(request)
    return await _call(
        lambda: service.start_training(
            body.workspace_id,
            body.export_id,
            body.base_path,
            approved=body.approved,
            iterations=body.iterations,
            timeout_s=body.timeout_s,
        )
    )


@router.post("/jobs/{job_id}/cancel", dependencies=[Depends(require_admin)])
async def cancel_job(job_id: str, request: Request, workspace_id: str) -> Any:
    service = _service(request)
    return await _call(lambda: service.cancel(workspace_id, job_id))


class RuntimeEvaluationRequest(BaseModel):
    model_config = {"extra": "forbid"}
    workspace_id: str
    export_id: str
    baseline_model: str = Field(min_length=1, max_length=200)
    candidate_model: str = Field(min_length=1, max_length=200)
    adapter_path: str
    approved: bool = False


@router.post("/evaluations/runtime", dependencies=[Depends(require_admin)])
async def evaluate_runtime(body: RuntimeEvaluationRequest, request: Request) -> Any:
    service = _service(request)
    return await _call(
        lambda: service.evaluate_runtime(
            body.workspace_id,
            body.export_id,
            body.baseline_model,
            body.candidate_model,
            body.adapter_path,
            approved=body.approved,
        )
    )
