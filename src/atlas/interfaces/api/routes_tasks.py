"""API routes for tasks.

HTTP semantics per the Gap Audit:
- POST /tasks → 202 Accepted (execution is async; client follows SSE for progress)
- POST /tasks/{task_id}/cancel → 200 with CancelTaskResponse (server decision, not fake success)
- GET  /tasks/{task_id}/events → supports ?after_sequence= cursor for gap recovery
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from atlas.interfaces.api.dependencies import get_control_plane
from atlas.interfaces.api.facade import AtlasControlPlane
from atlas.interfaces.api.schemas import (
    CancelTaskRequest,
    CancelTaskResponse,
    CreateTaskRequest,
    TaskEventResponse,
    TaskResponse,
)

router = APIRouter(tags=["tasks"])


@router.get("/tasks", response_model=list[TaskResponse])
async def list_tasks(
    limit: int = Query(default=20, le=100),
    control_plane: AtlasControlPlane = Depends(get_control_plane),
) -> list[TaskResponse]:
    """List recent tasks, newest first."""
    return await control_plane.get_tasks()


@router.post("/tasks", status_code=202)
async def create_task(
    command: CreateTaskRequest,
    control_plane: AtlasControlPlane = Depends(get_control_plane),
) -> JSONResponse:
    """Accept a task for async execution. Returns 202 with task in state 'created'.
    
    The task runs in the background. Connect to the SSE stream to follow progress.
    Idempotent: repeat calls with the same idempotency_key return the same task.
    """
    task = await control_plane.create_task(command)
    return JSONResponse(content=task.model_dump(mode="json"), status_code=202)


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: str,
    control_plane: AtlasControlPlane = Depends(get_control_plane),
) -> TaskResponse:
    """Get the current snapshot of a specific task."""
    return await control_plane.get_task(task_id)


@router.post("/tasks/{task_id}/cancel", response_model=CancelTaskResponse)
async def cancel_task(
    task_id: str,
    command: CancelTaskRequest,
    control_plane: AtlasControlPlane = Depends(get_control_plane),
) -> CancelTaskResponse:
    """Request cancellation of a running task.

    Returns the server's decision. 'accepted=True' means cancellation was
    signalled — the task may not stop immediately if a provider call is in flight.
    """
    return await control_plane.cancel_task(task_id, command)


@router.get("/tasks/{task_id}/events", response_model=list[TaskEventResponse])
async def get_task_events(
    task_id: str,
    after_sequence: int | None = Query(default=None, description="Return only events after this sequence number"),
    limit: int = Query(default=500, le=1000),
    control_plane: AtlasControlPlane = Depends(get_control_plane),
) -> list[TaskEventResponse]:
    """Return the ordered event history for a task.

    Use ?after_sequence=N to fetch only new events (cursor-based resync after SSE gap).
    """
    return await control_plane.task_events(task_id, after_sequence=after_sequence)
