"""Learning API — skills, strategies, world state, evaluation results.

Batch 6: exposes the learning subsystems (Batch 2/4) to the operator console.

Endpoints
---------
GET  /api/v1/learning/skills            Active + candidate skills
GET  /api/v1/learning/skills/{id}       Single skill
POST /api/v1/learning/skills/{id}/disable   Disable a skill (human decision)
GET  /api/v1/learning/strategies        Strategies (optionally only active)
GET  /api/v1/learning/world             World-state entities by type
GET  /api/v1/learning/evaluation/recent Recent evaluation runs
GET  /api/v1/learning/analytics         Aggregate learning analytics
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

router = APIRouter()


class SkillOut(BaseModel):
    id: str
    name: str
    description: str
    version: int
    status: str
    success_rate: float
    usage_count: int
    confidence: float
    preferred_tools: list[str]
    known_failure_modes: list[str]
    procedure_steps: list[str]
    updated_ts: str


class StrategyOut(BaseModel):
    id: str
    task_type_pattern: str
    approach: str
    model_preference: str | None
    tool_preference: list[str]
    status: str
    success_rate: float
    evidence_count: int
    eval_score: float | None
    updated_ts: str


class WorldEntityOut(BaseModel):
    entity_type: str
    entity_id: str
    attributes: dict[str, Any]
    updated_ts: str


class EvalResultOut(BaseModel):
    golden_id: str
    run_id: str
    evaluator: str
    passed: bool
    score: float
    created_ts: str


class LearningAnalytics(BaseModel):
    trajectory_success_rate: float | None
    total_trajectories: int
    total_experiences: int
    active_skills: int
    candidate_skills: int
    active_strategies: int
    recent_verification_pass_rate: float | None
    generated_at: str


@router.get("/learning/skills", response_model=list[SkillOut])
async def list_skills(
    request: Request,
    status: str | None = Query(None, description="filter: active | candidate | disabled"),
    limit: int = Query(50, ge=1, le=200),
) -> list[SkillOut]:
    atlas = request.app.state.atlas
    rows = await atlas.db.conn.execute(
        "SELECT * FROM skills WHERE (? IS NULL OR status = ?) AND superseded_by IS NULL "
        "ORDER BY confidence DESC, usage_count DESC LIMIT ?",
        (status, status, limit),
    )
    skills = await rows.fetchall()
    import json as _json

    return [
        SkillOut(
            id=r["id"],
            name=r["name"],
            description=r["description"],
            version=r["version"],
            status=r["status"],
            success_rate=r["success_rate"],
            usage_count=r["usage_count"],
            confidence=r["confidence"],
            preferred_tools=_json.loads(r["preferred_tools"]),
            known_failure_modes=_json.loads(r["known_failure_modes"]),
            procedure_steps=_json.loads(r["procedure_steps"]),
            updated_ts=r["updated_ts"],
        )
        for r in skills
    ]


@router.get("/learning/skills/{skill_id}", response_model=SkillOut)
async def get_skill(request: Request, skill_id: str) -> SkillOut:
    atlas = request.app.state.atlas
    skill = await atlas.skill_store.get(skill_id)
    if skill is None:
        raise HTTPException(404, f"skill {skill_id!r} not found")
    return SkillOut(
        id=skill.id,
        name=skill.name,
        description=skill.description,
        version=skill.version,
        status=skill.status,
        success_rate=skill.success_rate,
        usage_count=skill.usage_count,
        confidence=skill.confidence,
        preferred_tools=list(skill.preferred_tools),
        known_failure_modes=list(skill.known_failure_modes),
        procedure_steps=list(skill.procedure_steps),
        updated_ts=skill.updated_ts.isoformat(),
    )


@router.post("/learning/skills/{skill_id}/disable", response_model=SkillOut)
async def disable_skill(request: Request, skill_id: str) -> SkillOut:
    """Disable a skill. Human decision — never automatic."""
    atlas = request.app.state.atlas
    skill = await atlas.skill_store.get(skill_id)
    if skill is None:
        raise HTTPException(404, f"skill {skill_id!r} not found")
    from atlas.memory.skills import SkillStatus

    disabled = skill.model_copy(update={"status": SkillStatus.DISABLED})
    await atlas.skill_store.save(disabled)
    return SkillOut(
        id=disabled.id,
        name=disabled.name,
        description=disabled.description,
        version=disabled.version,
        status=disabled.status,
        success_rate=disabled.success_rate,
        usage_count=disabled.usage_count,
        confidence=disabled.confidence,
        preferred_tools=list(disabled.preferred_tools),
        known_failure_modes=list(disabled.known_failure_modes),
        procedure_steps=list(disabled.procedure_steps),
        updated_ts=disabled.updated_ts.isoformat(),
    )


@router.get("/learning/strategies", response_model=list[StrategyOut])
async def list_strategies(
    request: Request,
    active_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
) -> list[StrategyOut]:
    atlas = request.app.state.atlas
    where = "WHERE status = 'active'" if active_only else ""
    rows = await atlas.db.conn.execute(f"SELECT * FROM strategies {where} ORDER BY success_rate DESC LIMIT {limit}")
    strategies = await rows.fetchall()
    import json as _json

    return [
        StrategyOut(
            id=r["id"],
            task_type_pattern=r["task_type_pattern"],
            approach=r["approach"],
            model_preference=r["model_preference"],
            tool_preference=_json.loads(r["tool_preference"]),
            status=r["status"],
            success_rate=r["success_rate"],
            evidence_count=r["evidence_count"],
            eval_score=r["eval_score"],
            updated_ts=r["updated_ts"],
        )
        for r in strategies
    ]


@router.get("/learning/world", response_model=list[WorldEntityOut])
async def list_world(
    request: Request,
    entity_type: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
) -> list[WorldEntityOut]:
    atlas = request.app.state.atlas
    entities = (
        await atlas.world_state.by_type(entity_type, limit=limit) if entity_type else await _all_entities(atlas, limit)
    )
    return [
        WorldEntityOut(
            entity_type=e.entity_type,
            entity_id=e.entity_id,
            attributes=e.attributes,
            updated_ts=e.updated_ts.isoformat(),
        )
        for e in entities
    ]


async def _all_entities(atlas: Any, limit: int) -> list[Any]:
    rows = await atlas.db.conn.execute("SELECT * FROM world_state ORDER BY updated_ts DESC LIMIT ?", (limit,))
    raw = await rows.fetchall()
    import json as _json

    from atlas.memory.world_state import WorldEntity

    return [
        WorldEntity(
            entity_type=r["entity_type"],
            entity_id=r["entity_id"],
            attributes=_json.loads(r["attributes"]),
            updated_ts=datetime.fromisoformat(r["updated_ts"]),
        )
        for r in raw
    ]


@router.get("/learning/evaluation/recent", response_model=list[EvalResultOut])
async def recent_evaluations(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
) -> list[EvalResultOut]:
    atlas = request.app.state.atlas
    rows = await atlas.db.conn.execute(
        "SELECT golden_id, run_id, evaluator, passed, score, created_ts "
        "FROM evaluation_results ORDER BY created_ts DESC, rowid DESC LIMIT ?",
        (limit,),
    )
    return [
        EvalResultOut(
            golden_id=r["golden_id"],
            run_id=r["run_id"],
            evaluator=r["evaluator"],
            passed=bool(r["passed"]),
            score=r["score"],
            created_ts=r["created_ts"],
        )
        for r in await rows.fetchall()
    ]


@router.get("/learning/analytics", response_model=LearningAnalytics)
async def analytics(request: Request) -> LearningAnalytics:
    atlas = request.app.state.atlas
    conn = atlas.db.conn
    cur = await conn.execute("SELECT COUNT(*) AS n, AVG(success) AS rate FROM trajectories")
    traj = await cur.fetchone()
    cur = await conn.execute("SELECT COUNT(*) AS n FROM experiences WHERE superseded_by IS NULL")
    exp = await cur.fetchone()
    cur = await conn.execute("SELECT status, COUNT(*) AS n FROM skills GROUP BY status")
    skill_rows = {r["status"]: r["n"] for r in await cur.fetchall()}
    cur = await conn.execute("SELECT COUNT(*) AS n FROM strategies WHERE status = 'active'")
    st = await cur.fetchone()
    cur = await conn.execute(
        "SELECT AVG(verification_passed) AS rate FROM trajectories "
        "WHERE verification_passed IS NOT NULL AND completed_ts >= date('now', '-7 days')"
    )
    verif = await cur.fetchone()
    return LearningAnalytics(
        trajectory_success_rate=traj["rate"] if traj["n"] else None,
        total_trajectories=traj["n"],
        total_experiences=exp["n"],
        active_skills=skill_rows.get("active", 0),
        candidate_skills=skill_rows.get("candidate", 0),
        active_strategies=st["n"],
        recent_verification_pass_rate=verif["rate"],
        generated_at=datetime.now(UTC).isoformat(),
    )
