"""Durable execution fabric API (Part 4 §99) — run status + control.

Endpoints
---------
GET  /api/v1/execution/runs                     Recent runs
GET  /api/v1/execution/runs/{run_id}            Full run state (§6)
POST /api/v1/execution/runs/{run_id}/pause      Cooperative pause (§47)
POST /api/v1/execution/runs/{run_id}/resume     Resume from checkpoint (§21/§68)
POST /api/v1/execution/runs/{run_id}/cancel     Cooperative cancellation (§45)
POST /api/v1/execution/runs/{run_id}/retry      Re-arm a failed run (§99)

RoutePlan execution START is available programmatically
(`execution_engine.start(...)`); exposing plan-start over HTTP arrives with the
plan-submission surface, keeping this API read/control only.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


@router.get("/execution/runs")
async def list_runs(request: Request, limit: int = 20) -> list[dict[str, Any]]:
    engine = _engine(request)
    return await engine.list_runs(limit=min(max(limit, 1), 100))  # type: ignore[no-any-return]


@router.get("/execution/runs/{run_id}")
async def get_run(run_id: str, request: Request) -> dict[str, Any]:
    run = await _engine(request).status(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"execution run {run_id!r} not found")
    out: dict[str, Any] = dict(run.model_dump(mode="json"))
    return out


@router.post("/execution/runs/{run_id}/pause")
async def pause_run(run_id: str, request: Request) -> dict[str, Any]:
    ok = await _engine(request).pause(run_id)
    return {"paused": ok}


@router.post("/execution/runs/{run_id}/resume")
async def resume_run(run_id: str, request: Request) -> dict[str, Any]:
    result = await _engine(request).resume(run_id)
    if result is None:
        raise HTTPException(status_code=409, detail=f"run {run_id!r} is not resumable (no checkpoint or not waiting)")
    out: dict[str, Any] = dict(result.model_dump(mode="json"))
    return out


@router.post("/execution/runs/{run_id}/cancel")
async def cancel_run(run_id: str, request: Request) -> dict[str, Any]:
    ok = await _engine(request).cancel(run_id)
    return {"cancel_requested": ok}


@router.post("/execution/runs/{run_id}/retry")
async def retry_run(run_id: str, request: Request) -> dict[str, Any]:
    result = await _engine(request).retry(run_id)
    if result is None:
        raise HTTPException(status_code=409, detail=f"run {run_id!r} is not retryable")
    out: dict[str, Any] = dict(result.model_dump(mode="json"))
    return out


def _engine(request: Request) -> Any:
    engine = getattr(request.app.state.atlas, "execution_engine", None)
    if engine is None:
        raise HTTPException(status_code=503, detail="execution fabric is not available")
    return engine
