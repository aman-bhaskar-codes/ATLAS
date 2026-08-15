"""Trajectory & Experience API — Phase 2 durable learning endpoints.

REST endpoints for querying trajectories, decision traces, failure records,
and extracted experiences. Follows routes_memory.py patterns for consistency.

Endpoints
---------
GET  /api/v1/trajectory/trajectories    Query trajectories with filters
GET  /api/v1/trajectory/{id}            Get single trajectory
GET  /api/v1/trajectory/task/{task_id}  Get trajectory by task_id
GET  /api/v1/trajectory/recent          Recent trajectories (20 by default)
GET  /api/v1/trajectory/failed          Failed trajectories for analysis
GET  /api/v1/trajectory/decisions       Query decision traces
GET  /api/v1/trajectory/failures        Query failure records
GET  /api/v1/trajectory/failures/patterns  Failure pattern analysis
GET  /api/v1/trajectory/experiences     Query extracted experiences
GET  /api/v1/trajectory/stats           Aggregate trajectory stats
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from atlas.infra.logging import get_logger

_log = get_logger("atlas.api.trajectory")

router = APIRouter()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Response Models
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TrajectoryOut(BaseModel):
    """Complete trajectory with execution history."""
    id: str
    task_id: str
    correlation_id: str
    request: str
    goal: str
    plan_steps: list[str]
    risk_level: str
    plan_confidence: float
    success: bool
    answer: str | None
    error: str | None
    steps_taken: int
    replan_count: int
    verification_passed: bool | None
    verification_score: float | None
    latency_ms: int
    tokens_used: int
    cost_usd: float
    model_calls: int
    tool_calls: int
    created_ts: str
    completed_ts: str
    # Optional: full action/observation history (can be large)
    actions: list[dict[str, Any]] | None = None
    observations: list[dict[str, Any]] | None = None


class TrajectoryS ummaryOut(BaseModel):
    """Lightweight trajectory summary without actions/observations."""
    id: str
    task_id: str
    goal: str
    success: bool
    steps_taken: int
    replan_count: int
    latency_ms: int
    completed_ts: str


class DecisionTraceOut(BaseModel):
    """A single decision point record."""
    id: str
    task_id: str
    ts: str
    decision_point: str
    options_considered: list[str]
    chosen_option: str
    rationale: str
    outcome: str
    outcome_detail: str | None
    confidence: float
    latency_ms: int | None
    cost_usd: float


class FailureRecordOut(BaseModel):
    """A structured failure record."""
    id: str
    task_id: str
    ts: str
    category: str
    step: int
    component: str
    error_message: str
    recovered: bool
    recovery_method: str | None
    recovery_succeeded: bool
    mitigation_suggested: str | None


class FailurePatternOut(BaseModel):
    """Aggregated failure pattern statistics."""
    category: str
    component: str
    occurrence_count: int
    recovery_count: int
    recovery_success_count: int
    recovery_rate: float


class ExperienceOut(BaseModel):
    """An extracted lesson from trajectory analysis."""
    id: str
    trajectory_id: str
    task_id: str
    category: str
    lesson_text: str
    applicability_context: str
    confidence: float
    reuse_count: int
    success_rate: float
    avg_improvement_ms: int
    avg_cost_savings_usd: float
    extracted_ts: str
    last_applied_ts: str | None
    superseded_by: str | None


class TrajectoryStatsOut(BaseModel):
    """Aggregate trajectory statistics."""
    total_trajectories: int
    successful_trajectories: int
    failed_trajectories: int
    total_replans: int
    avg_steps: float
    avg_latency_ms: float
    total_experiences: int
    total_failures: int


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Trajectory Endpoints
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@router.get("/api/v1/trajectory/trajectories", response_model=list[TrajectorySummaryOut])
async def query_trajectories(
    request: Request,
    task_id: str | None = Query(default=None),
    correlation_id: str | None = Query(default=None),
    success: bool | None = Query(default=None),
    min_replan_count: int = Query(default=0, ge=0),
    min_steps: int = Query(default=0, ge=0),
    min_latency_ms: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[TrajectorySummaryOut]:
    """
    Query trajectories with filters.
    Returns summaries without full action/observation history.
    Performance target: < 50ms.
    """
    atlas = request.app.state.atlas
    trajectory_store = atlas.trajectory_store
    
    from atlas.memory.trajectory import TrajectoryQuery
    
    query = TrajectoryQuery(
        task_id=task_id,
        correlation_id=correlation_id,
        success=success,
        min_replan_count=min_replan_count,
        min_steps=min_steps,
        min_latency_ms=min_latency_ms,
        limit=limit,
    )
    
    trajectories = await trajectory_store.query_trajectories(query)
    
    return [
        TrajectorySummaryOut(
            id=t.id,
            task_id=t.task_id,
            goal=t.goal,
            success=t.success,
            steps_taken=t.steps_taken,
            replan_count=t.replan_count,
            latency_ms=t.latency_ms,
            completed_ts=t.completed_ts.isoformat(),
        )
        for t in trajectories
    ]


@router.get("/api/v1/trajectory/{trajectory_id}", response_model=TrajectoryOut)
async def get_trajectory(
    request: Request,
    trajectory_id: str,
    include_history: bool = Query(default=False),
) -> TrajectoryOut:
    """
    Get single trajectory by ID.
    Set include_history=true to get full action/observation arrays.
    """
    atlas = request.app.state.atlas
    trajectory_store = atlas.trajectory_store
    
    trajectory = await trajectory_store.get_trajectory(trajectory_id)
    
    if not trajectory:
        raise HTTPException(status_code=404, detail="Trajectory not found")
    
    return TrajectoryOut(
        id=trajectory.id,
        task_id=trajectory.task_id,
        correlation_id=trajectory.correlation_id,
        request=trajectory.request,
        goal=trajectory.goal,
        plan_steps=list(trajectory.plan_steps),
        risk_level=trajectory.risk_level,
        plan_confidence=trajectory.plan_confidence,
        success=trajectory.success,
        answer=trajectory.answer,
        error=trajectory.error,
        steps_taken=trajectory.steps_taken,
        replan_count=trajectory.replan_count,
        verification_passed=trajectory.verification_passed,
        verification_score=trajectory.verification_score,
        latency_ms=trajectory.latency_ms,
        tokens_used=trajectory.tokens_used,
        cost_usd=trajectory.cost_usd,
        model_calls=trajectory.model_calls,
        tool_calls=trajectory.tool_calls,
        created_ts=trajectory.created_ts.isoformat(),
        completed_ts=trajectory.completed_ts.isoformat(),
        actions=[a.model_dump() for a in trajectory.actions] if include_history else None,
        observations=[o.model_dump() for o in trajectory.observations] if include_history else None,
    )


@router.get("/api/v1/trajectory/task/{task_id}", response_model=TrajectoryOut)
async def get_trajectory_by_task(
    request: Request,
    task_id: str,
    include_history: bool = Query(default=True),
) -> TrajectoryOut:
    """
    Get trajectory by task_id (one-to-one relationship).
    Useful for displaying trajectory in task detail views.
    """
    atlas = request.app.state.atlas
    trajectory_store = atlas.trajectory_store
    
    trajectory = await trajectory_store.get_trajectory_by_task(task_id)
    
    if not trajectory:
        raise HTTPException(status_code=404, detail="Trajectory not found for task")
    
    return TrajectoryOut(
        id=trajectory.id,
        task_id=trajectory.task_id,
        correlation_id=trajectory.correlation_id,
        request=trajectory.request,
        goal=trajectory.goal,
        plan_steps=list(trajectory.plan_steps),
        risk_level=trajectory.risk_level,
        plan_confidence=trajectory.plan_confidence,
        success=trajectory.success,
        answer=trajectory.answer,
        error=trajectory.error,
        steps_taken=trajectory.steps_taken,
        replan_count=trajectory.replan_count,
        verification_passed=trajectory.verification_passed,
        verification_score=trajectory.verification_score,
        latency_ms=trajectory.latency_ms,
        tokens_used=trajectory.tokens_used,
        cost_usd=trajectory.cost_usd,
        model_calls=trajectory.model_calls,
        tool_calls=trajectory.tool_calls,
        created_ts=trajectory.created_ts.isoformat(),
        completed_ts=trajectory.completed_ts.isoformat(),
        actions=[a.model_dump() for a in trajectory.actions] if include_history else None,
        observations=[o.model_dump() for o in trajectory.observations] if include_history else None,
    )


@router.get("/api/v1/trajectory/recent", response_model=list[TrajectorySummaryOut])
async def get_recent_trajectories(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[TrajectorySummaryOut]:
    """
    Get most recent trajectories.
    Performance target: < 20ms.
    """
    atlas = request.app.state.atlas
    trajectory_store = atlas.trajectory_store
    
    trajectories = await trajectory_store.get_recent_trajectories(limit=limit)
    
    return [
        TrajectorySummaryOut(
            id=t.id,
            task_id=t.task_id,
            goal=t.goal,
            success=t.success,
            steps_taken=t.steps_taken,
            replan_count=t.replan_count,
            latency_ms=t.latency_ms,
            completed_ts=t.completed_ts.isoformat(),
        )
        for t in trajectories
    ]


@router.get("/api/v1/trajectory/failed", response_model=list[TrajectorySummaryOut])
async def get_failed_trajectories(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[TrajectorySummaryOut]:
    """
    Get failed trajectories for analysis.
    Useful for debugging and failure taxonomy building.
    """
    atlas = request.app.state.atlas
    trajectory_store = atlas.trajectory_store
    
    trajectories = await trajectory_store.get_failed_trajectories(limit=limit)
    
    return [
        TrajectorySummaryOut(
            id=t.id,
            task_id=t.task_id,
            goal=t.goal,
            success=t.success,
            steps_taken=t.steps_taken,
            replan_count=t.replan_count,
            latency_ms=t.latency_ms,
            completed_ts=t.completed_ts.isoformat(),
        )
        for t in trajectories
    ]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Decision Trace Endpoints
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@router.get("/api/v1/trajectory/decisions", response_model=list[DecisionTraceOut])
async def query_decision_traces(
    request: Request,
    task_id: str | None = Query(default=None),
    decision_point: str | None = Query(default=None),
    outcome: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[DecisionTraceOut]:
    """
    Query decision traces with filters.
    Useful for analyzing which choices work in which contexts.
    """
    atlas = request.app.state.atlas
    trajectory_store = atlas.trajectory_store
    
    from atlas.memory.trajectory import DecisionOutcome, DecisionPoint
    
    traces = await trajectory_store.get_decision_traces(
        task_id=task_id,
        decision_point=DecisionPoint(decision_point) if decision_point else None,
        outcome=DecisionOutcome(outcome) if outcome else None,
        limit=limit,
    )
    
    return [
        DecisionTraceOut(
            id=t.id,
            task_id=t.task_id,
            ts=t.ts.isoformat(),
            decision_point=t.decision_point.value,
            options_considered=list(t.options_considered),
            chosen_option=t.chosen_option,
            rationale=t.rationale,
            outcome=t.outcome.value,
            outcome_detail=t.outcome_detail,
            confidence=t.confidence,
            latency_ms=t.latency_ms,
            cost_usd=t.cost_usd,
        )
        for t in traces
    ]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Failure Record Endpoints
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@router.get("/api/v1/trajectory/failures", response_model=list[FailureRecordOut])
async def query_failure_records(
    request: Request,
    task_id: str | None = Query(default=None),
    category: str | None = Query(default=None),
    component: str | None = Query(default=None),
    recovered_only: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[FailureRecordOut]:
    """
    Query failure records with filters.
    Set recovered_only=true to see only successfully recovered failures.
    """
    atlas = request.app.state.atlas
    trajectory_store = atlas.trajectory_store
    
    from atlas.memory.trajectory import FailureCategory
    
    failures = await trajectory_store.get_failure_records(
        task_id=task_id,
        category=FailureCategory(category) if category else None,
        component=component,
        recovered_only=recovered_only,
        limit=limit,
    )
    
    return [
        FailureRecordOut(
            id=f.id,
            task_id=f.task_id,
            ts=f.ts.isoformat(),
            category=f.category.value,
            step=f.step,
            component=f.component,
            error_message=f.error_message,
            recovered=f.recovered,
            recovery_method=f.recovery_method,
            recovery_succeeded=f.recovery_succeeded,
            mitigation_suggested=f.mitigation_suggested,
        )
        for f in failures
    ]


@router.get("/api/v1/trajectory/failures/patterns", response_model=list[FailurePatternOut])
async def get_failure_patterns(
    request: Request,
    category: str = Query(...),
    min_occurrences: int = Query(default=3, ge=2, le=100),
) -> list[FailurePatternOut]:
    """
    Identify recurring failure patterns by category.
    Returns aggregated statistics for failure taxonomy building.
    """
    atlas = request.app.state.atlas
    trajectory_store = atlas.trajectory_store
    
    from atlas.memory.trajectory import FailureCategory
    
    patterns = await trajectory_store.get_failure_patterns(
        category=FailureCategory(category),
        min_occurrences=min_occurrences,
    )
    
    return [
        FailurePatternOut(
            category=str(p["category"]),
            component=str(p["component"]),
            occurrence_count=int(p["occurrence_count"]),
            recovery_count=int(p["recovery_count"]),
            recovery_success_count=int(p["recovery_success_count"]),
            recovery_rate=(
                float(p["recovery_success_count"]) / float(p["recovery_count"])
                if p["recovery_count"] > 0 else 0.0
            ),
        )
        for p in patterns
    ]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Experience Endpoints
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@router.get("/api/v1/trajectory/experiences", response_model=list[ExperienceOut])
async def query_experiences(
    request: Request,
    category: str | None = Query(default=None),
    min_confidence: float = Query(default=0.5, ge=0.0, le=1.0),
    min_reuse_count: int = Query(default=0, ge=0),
    min_success_rate: float = Query(default=0.0, ge=0.0, le=1.0),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[ExperienceOut]:
    """
    Query extracted experiences with filters.
    Returns lessons learned from trajectory analysis.
    Excludes superseded experiences by default.
    """
    atlas = request.app.state.atlas
    trajectory_store = atlas.trajectory_store
    
    from atlas.memory.trajectory import ExperienceCategory, ExperienceQuery
    
    query = ExperienceQuery(
        category=ExperienceCategory(category) if category else None,
        min_confidence=min_confidence,
        min_reuse_count=min_reuse_count,
        min_success_rate=min_success_rate,
        limit=limit,
    )
    
    experiences = await trajectory_store.query_experiences(query)
    
    return [
        ExperienceOut(
            id=e.id,
            trajectory_id=e.trajectory_id,
            task_id=e.task_id,
            category=e.category.value,
            lesson_text=e.lesson_text,
            applicability_context=e.applicability_context,
            confidence=e.confidence,
            reuse_count=e.reuse_count,
            success_rate=e.success_rate,
            avg_improvement_ms=e.avg_improvement_ms,
            avg_cost_savings_usd=e.avg_cost_savings_usd,
            extracted_ts=e.extracted_ts.isoformat(),
            last_applied_ts=e.last_applied_ts.isoformat() if e.last_applied_ts else None,
            superseded_by=e.superseded_by,
        )
        for e in experiences
    ]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Stats Endpoint
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@router.get("/api/v1/trajectory/stats", response_model=TrajectoryStatsOut)
async def get_trajectory_stats(request: Request) -> TrajectoryStatsOut:
    """
    Aggregate trajectory statistics for dashboard cards.
    Performance target: < 100ms.
    """
    atlas = request.app.state.atlas
    db = atlas.db
    
    # Run queries in parallel
    import asyncio
    
    total, successful, failed, total_replans, total_steps, total_latency, total_exp, total_fail = await asyncio.gather(
        _count_trajectories(db),
        _count_successful(db),
        _count_failed(db),
        _sum_replans(db),
        _sum_steps(db),
        _sum_latency(db),
        _count_experiences(db),
        _count_failures(db),
    )
    
    avg_steps = float(total_steps) / float(total) if total > 0 else 0.0
    avg_latency = float(total_latency) / float(total) if total > 0 else 0.0
    
    return TrajectoryStatsOut(
        total_trajectories=total,
        successful_trajectories=successful,
        failed_trajectories=failed,
        total_replans=total_replans,
        avg_steps=avg_steps,
        avg_latency_ms=avg_latency,
        total_experiences=total_exp,
        total_failures=total_fail,
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Private Helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


async def _count_trajectories(db: Any) -> int:
    cur = await db.conn.execute("SELECT COUNT(*) FROM trajectories")
    row = await cur.fetchone()
    return int(row[0]) if row else 0


async def _count_successful(db: Any) -> int:
    cur = await db.conn.execute("SELECT COUNT(*) FROM trajectories WHERE success = 1")
    row = await cur.fetchone()
    return int(row[0]) if row else 0


async def _count_failed(db: Any) -> int:
    cur = await db.conn.execute("SELECT COUNT(*) FROM trajectories WHERE success = 0")
    row = await cur.fetchone()
    return int(row[0]) if row else 0


async def _sum_replans(db: Any) -> int:
    cur = await db.conn.execute("SELECT SUM(replan_count) FROM trajectories")
    row = await cur.fetchone()
    return int(row[0]) if row and row[0] else 0


async def _sum_steps(db: Any) -> int:
    cur = await db.conn.execute("SELECT SUM(steps_taken) FROM trajectories")
    row = await cur.fetchone()
    return int(row[0]) if row and row[0] else 0


async def _sum_latency(db: Any) -> int:
    cur = await db.conn.execute("SELECT SUM(latency_ms) FROM trajectories")
    row = await cur.fetchone()
    return int(row[0]) if row and row[0] else 0


async def _count_experiences(db: Any) -> int:
    cur = await db.conn.execute(
        "SELECT COUNT(*) FROM experiences WHERE superseded_by IS NULL"
    )
    row = await cur.fetchone()
    return int(row[0]) if row else 0


async def _count_failures(db: Any) -> int:
    cur = await db.conn.execute("SELECT COUNT(*) FROM failure_records")
    row = await cur.fetchone()
    return int(row[0]) if row else 0
