"""Feedback API routes — Vamos evaluation loop.

POST /feedback          — Record user feedback (thumbs up/down + optional edit)
GET  /feedback/stats    — Aggregate feedback statistics
GET  /audit/verify      — Verify audit hash chain integrity
GET  /schedules         — List all schedules
POST /schedules         — Create a new recurring schedule
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from atlas.app import Atlas
from atlas.interfaces.api.dependencies import get_atlas

router = APIRouter(tags=["feedback"])


class FeedbackRequest(BaseModel):
    task_id: str
    rating: int = Field(..., description="1 for thumbs up, -1 for thumbs down")
    comment: str | None = None
    original_output: str | None = None
    edited_output: str | None = None


class ScheduleRequest(BaseModel):
    description: str
    cron_expression: str
    task_template: dict[str, object]


@router.post("/feedback")
async def submit_feedback(
    req: FeedbackRequest,
    atlas: Atlas = Depends(get_atlas),
) -> dict[str, str]:
    """Record user feedback on a task outcome."""
    if atlas.feedback is None:
        raise HTTPException(503, "Feedback store not initialized")
    try:
        fid = await atlas.feedback.record(
            task_id=req.task_id, rating=req.rating,
            comment=req.comment,
            original_output=req.original_output,
            edited_output=req.edited_output,
        )
        return {"id": fid, "status": "recorded"}
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.get("/feedback/stats")
async def feedback_stats(
    atlas: Atlas = Depends(get_atlas),
) -> dict[str, int]:
    """Get aggregate feedback statistics."""
    if atlas.feedback is None:
        raise HTTPException(503, "Feedback store not initialized")
    return await atlas.feedback.stats()


@router.get("/audit/verify")
async def verify_audit_chain(
    atlas: Atlas = Depends(get_atlas),
) -> dict[str, object]:
    """Verify the SHA-256 hash chain integrity of the audit log."""
    valid, count = await atlas.audit.verify_chain()
    return {
        "chain_valid": valid,
        "records_verified": count,
        "status": "✓ Audit chain intact" if valid else "✗ CHAIN BROKEN — tampering detected",
    }


@router.get("/schedules")
async def list_schedules(
    atlas: Atlas = Depends(get_atlas),
) -> dict[str, object]:
    """List all recurring schedules."""
    if atlas.scheduler is None:
        raise HTTPException(503, "Scheduler not initialized")
    schedules = await atlas.scheduler.list_schedules()
    return {"schedules": schedules, "count": len(schedules)}


@router.post("/schedules")
async def create_schedule(
    req: ScheduleRequest,
    atlas: Atlas = Depends(get_atlas),
) -> dict[str, str]:
    """Create a new recurring schedule."""
    if atlas.scheduler is None:
        raise HTTPException(503, "Scheduler not initialized")
    sid = await atlas.scheduler.add_schedule(
        description=req.description,
        cron_expression=req.cron_expression,
        task_template=req.task_template,
    )
    return {"id": sid, "status": "created"}
