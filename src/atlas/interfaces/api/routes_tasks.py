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
)

router = APIRouter(tags=["tasks"])

# NOTE: GET /tasks and GET /tasks/{task_id} used to live here too, returning
# the thinner TaskResponse schema. They were silently shadowed at runtime by
# routes_trust.py's TaskPage/TaskView versions of the same paths (registered
# after this router in app.py, but that router additionally declares its own
# "/api/v1" prefix, which collided with this router's mount prefix). Rather
# than leave dead, never-executed handlers in the codebase, they were removed
# here. routes_trust.py is now the single owner of task reads; this router
# owns only the write/action endpoints below, which routes_trust.py does not
# implement.


@router.post("/tasks", status_code=202)
async def create_task(
    command: CreateTaskRequest,
    control_plane: AtlasControlPlane = Depends(get_control_plane),
) -> JSONResponse:
    """Accept a task for async execution. Returns 202 with task in state 'created'.

    The task runs in the background. Connect to the SSE stream to follow progress.
    Idempotent: repeat calls with the same idempotency_key return the same task.
    """
    if command.attachments:
        att_summary = "\n\n[Attachments Provided:\n"
        for att in command.attachments:
            att_summary += f"- {att.type} ({att.id})\n"
        att_summary += "]"
        # Update the request string because Task schema is frozen.
        command = command.model_copy(update={"request": command.request + att_summary})

    task = await control_plane.create_task(command)
    return JSONResponse(content=task.model_dump(mode="json"), status_code=202)


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
