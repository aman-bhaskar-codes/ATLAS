"""FastAPI transport for Trust Center projections and commands."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import AwareDatetime

from atlas.interfaces.api.control_plane import AtlasTrustPlane
from atlas.interfaces.api.dependencies import get_trust_plane
from atlas.interfaces.api.schemas_trust import (
    ApprovalView,
    AuditExport,
    AuditPage,
    MemoryCorrectionCommand,
    MemoryFactView,
    MemoryMutationReceipt,
    TaskPage,
    TaskView,
)

router = APIRouter(prefix="/api/v1", tags=["trust"])


@router.get("/tasks", response_model=TaskPage)
async def tasks(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    plane: AtlasTrustPlane = Depends(get_trust_plane),
) -> TaskPage:
    return await plane.list_tasks(cursor=cursor, limit=limit)


@router.get("/tasks/{task_id}", response_model=TaskView)
async def task(task_id: str, plane: AtlasTrustPlane = Depends(get_trust_plane)) -> TaskView:
    return await plane.get_task(task_id)


# Single-ticket read uses the same durable approval projection as the action router.
@router.get("/approvals/{approval_id}", response_model=ApprovalView)
async def approval(approval_id: str, plane: AtlasTrustPlane = Depends(get_trust_plane)) -> ApprovalView:
    return await plane.get_approval(approval_id)


@router.get("/memory/search", response_model=tuple[MemoryFactView, ...])
async def memory_search(
    q: str = Query(default="", max_length=500),
    limit: int = Query(default=30, ge=1, le=100),
    plane: AtlasTrustPlane = Depends(get_trust_plane),
) -> tuple[MemoryFactView, ...]:
    return tuple(await plane.search_memory(q, limit=limit))


@router.get("/memory/facts/{fact_id}", response_model=MemoryFactView)
async def memory_fact(fact_id: str, plane: AtlasTrustPlane = Depends(get_trust_plane)) -> MemoryFactView:
    return await plane.get_memory_fact(fact_id)


@router.post("/memory/facts/{fact_id}/correct", response_model=MemoryMutationReceipt)
async def correct_memory(
    fact_id: str,
    command: MemoryCorrectionCommand,
    plane: AtlasTrustPlane = Depends(get_trust_plane),
) -> MemoryMutationReceipt:
    return await plane.correct_memory(command.model_copy(update={"fact_id": fact_id}))


@router.get("/audit", response_model=AuditPage)
async def audit(
    task_id: str | None = None,
    correlation_id: str | None = None,
    execution_id: str | None = None,
    cursor: str | None = Query(default=None, pattern=r"^[1-9][0-9]*$", max_length=20),
    limit: int = Query(default=50, ge=1, le=100),
    since: AwareDatetime | None = None,
    tier: int | None = Query(default=None, ge=0, le=4),
    plane: AtlasTrustPlane = Depends(get_trust_plane),
) -> AuditPage:
    return await plane.audit(
        task_id=task_id,
        correlation_id=correlation_id,
        execution_id=execution_id,
        cursor=cursor,
        limit=limit,
        since=since,
        tier=tier,
    )


@router.get("/audit/export", response_model=AuditExport)
async def export_audit(
    since: AwareDatetime | None = None,
    tier: int | None = Query(default=None, ge=0, le=4),
    max_events: int = Query(default=1000, ge=1, le=1000),
    plane: AtlasTrustPlane = Depends(get_trust_plane),
) -> AuditExport:
    return await plane.export_audit(since=since, tier=tier, max_events=max_events)
