"""FastAPI transport for Trust Center projections and commands."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from atlas.interfaces.api.control_plane import AtlasTrustPlane
from atlas.interfaces.api.dependencies import get_trust_plane
from atlas.interfaces.api.schemas_trust import (
    ApprovalView, AuditPage, MemoryCorrectionCommand,
    MemoryFactView, MemoryMutationReceipt, TaskPage, TaskView,
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


# NOTE: /approvals/pending and /approvals/{id}/decide used to be duplicated
# here as well as in routes_approvals.py, on identical paths, with a
# different response schema (ApprovalView vs ApprovalResponse). Both
# implementations are functionally stubs today — AtlasControlPlane's is a
# documented placeholder pending approval storage (see facade.py), and
# AtlasTrustPlane's raised NotImplementedError unconditionally. Keeping two
# non-working implementations of the same feature added confusion without
# adding capability, so this router keeps only the single-approval lookup,
# which routes_approvals.py does not implement at all.
#
# STATUS: GET /approvals/{approval_id} below always raises KeyError today —
# AtlasTrustPlane.get_approval() is unimplemented pending the same approval
# storage work as pending_approvals()/decide_approval(). This is a known,
# tracked gap, not something this fix silently papers over.
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
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    plane: AtlasTrustPlane = Depends(get_trust_plane),
) -> AuditPage:
    return await plane.audit(
        task_id=task_id, correlation_id=correlation_id,
        execution_id=execution_id, cursor=cursor, limit=limit,
    )
