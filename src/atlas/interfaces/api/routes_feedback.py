"""Feedback API routes — Vamos evaluation loop.

POST /feedback          — Record user feedback (thumbs up/down + optional edit)
GET  /feedback/stats    — Aggregate feedback statistics
GET  /audit/verify      — Verify audit hash chain integrity
GET  /schedules         — List all schedules
POST /schedules         — Create a new recurring schedule
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

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
    task_template: dict


@router.post("/feedback")
async def submit_feedback(req: FeedbackRequest) -> dict:
    """Record user feedback on a task outcome."""
    atlas = get_atlas()
    if not hasattr(atlas, "feedback") or atlas.feedback is None:
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
        raise HTTPException(422, str(e))


@router.get("/feedback/stats")
async def feedback_stats() -> dict:
    """Get aggregate feedback statistics."""
    atlas = get_atlas()
    if not hasattr(atlas, "feedback") or atlas.feedback is None:
        raise HTTPException(503, "Feedback store not initialized")
    return await atlas.feedback.stats()


@router.get("/audit/verify")
async def verify_audit_chain() -> dict:
    """Verify the SHA-256 hash chain integrity of the audit log."""
    atlas = get_atlas()
    valid, count = await atlas.audit.verify_chain()
    return {
        "chain_valid": valid,
        "records_verified": count,
        "status": "✓ Audit chain intact" if valid else "✗ CHAIN BROKEN — tampering detected",
    }


@router.get("/schedules")
async def list_schedules() -> dict:
    """List all recurring schedules."""
    atlas = get_atlas()
    if not hasattr(atlas, "scheduler") or atlas.scheduler is None:
        raise HTTPException(503, "Scheduler not initialized")
    schedules = await atlas.scheduler.list_schedules()
    return {"schedules": schedules, "count": len(schedules)}


@router.post("/schedules")
async def create_schedule(req: ScheduleRequest) -> dict:
    """Create a new recurring schedule."""
    atlas = get_atlas()
    if not hasattr(atlas, "scheduler") or atlas.scheduler is None:
        raise HTTPException(503, "Scheduler not initialized")
    sid = await atlas.scheduler.add_schedule(
        description=req.description,
        cron_expression=req.cron_expression,
        task_template=req.task_template,
    )
    return {"id": sid, "status": "created"}
