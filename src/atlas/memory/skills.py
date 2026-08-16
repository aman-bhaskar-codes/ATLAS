"""Skill library — reusable procedures promoted from evidence.

WHY skills: experiences are single lessons; a skill is a PROCEDURE the agent
has learned works for a class of tasks. Promotion is evidence-gated: a
candidate skill becomes active only after enough successful applications, and
is demoted when its success rate collapses. Skills influence PLANNING (prompt
context) only — they never alter safety policy or bypass the Safety Engine.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from atlas.infra.clock import Clock
from atlas.infra.db import Database
from atlas.infra.ids import IdGenerator
from atlas.infra.logging import get_logger

_log = get_logger("atlas.memory.skills")

# Evidence thresholds — deliberately conservative. One lucky run must never
# change agent behavior.
PROMOTION_MIN_APPLICATIONS = 3
PROMOTION_MIN_SUCCESS_RATE = 0.7
DEMOTION_MAX_SUCCESS_RATE = 0.3
DEMOTION_MIN_APPLICATIONS = 5


class SkillStatus:
    CANDIDATE = "candidate"
    ACTIVE = "active"
    DISABLED = "disabled"


class Skill(BaseModel):
    model_config = {"frozen": True}

    id: str
    name: str
    description: str
    procedure_steps: tuple[str, ...] = ()
    version: int = 1
    status: str = SkillStatus.CANDIDATE
    success_rate: float = 0.0
    usage_count: int = 0
    confidence: float = 0.5
    preferred_tools: tuple[str, ...] = ()
    known_failure_modes: tuple[str, ...] = ()
    source_experience_ids: tuple[str, ...] = ()
    created_ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    superseded_by: str | None = None

    def to_prompt_fragment(self) -> str:
        lines = [f"Skill '{self.name}' (confidence {self.confidence:.2f}): {self.description}"]
        for i, step in enumerate(self.procedure_steps, 1):
            lines.append(f"  {i}. {step}")
        if self.preferred_tools:
            lines.append(f"  Preferred tools: {', '.join(self.preferred_tools)}")
        return "\n".join(lines)


class SkillStore:
    """SQLite CRUD + evidence-based promotion/demotion for skills."""

    def __init__(self, db: Database, ids: IdGenerator, clock: Clock) -> None:
        self._db = db
        self._ids = ids
        self._clock = clock

    async def save(self, skill: Skill) -> str:
        now = self._clock.now().isoformat()
        await self._db.conn.execute(
            "INSERT OR REPLACE INTO skills (id, name, description, procedure_steps, "
            "version, status, success_rate, usage_count, confidence, preferred_tools, "
            "known_failure_modes, source_experience_ids, created_ts, updated_ts, superseded_by) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                skill.id,
                skill.name,
                skill.description,
                json.dumps(list(skill.procedure_steps)),
                skill.version,
                skill.status,
                skill.success_rate,
                skill.usage_count,
                skill.confidence,
                json.dumps(list(skill.preferred_tools)),
                json.dumps(list(skill.known_failure_modes)),
                json.dumps(list(skill.source_experience_ids)),
                skill.created_ts.isoformat(),
                now,
                skill.superseded_by,
            ),
        )
        await self._db.conn.commit()
        return skill.id

    async def get(self, skill_id: str) -> Skill | None:
        cur = await self._db.conn.execute("SELECT * FROM skills WHERE id = ?", (skill_id,))
        row = await cur.fetchone()
        return self._from_row(row) if row else None

    async def active_skills(self, limit: int = 20) -> list[Skill]:
        cur = await self._db.conn.execute(
            "SELECT * FROM skills WHERE status = 'active' AND superseded_by IS NULL "
            "ORDER BY confidence DESC, usage_count DESC LIMIT ?",
            (limit,),
        )
        rows = await cur.fetchall()
        return [self._from_row(r) for r in rows]

    async def find_by_name(self, name: str) -> Skill | None:
        cur = await self._db.conn.execute(
            "SELECT * FROM skills WHERE name = ? AND superseded_by IS NULL ORDER BY version DESC LIMIT 1",
            (name,),
        )
        row = await cur.fetchone()
        return self._from_row(row) if row else None

    async def record_application(self, skill_id: str, *, success: bool) -> Skill | None:
        """Record one application and re-evaluate promotion/demotion status."""
        skill = await self.get(skill_id)
        if skill is None:
            return None
        usage = skill.usage_count + 1
        rate = (skill.success_rate * skill.usage_count + (1.0 if success else 0.0)) / usage
        status = skill.status
        if (
            status == SkillStatus.CANDIDATE
            and usage >= PROMOTION_MIN_APPLICATIONS
            and rate >= PROMOTION_MIN_SUCCESS_RATE
        ):
            status = SkillStatus.ACTIVE
            _log.info("skill.promoted", event_type="memory", skill_id=skill_id, usage=usage, success_rate=rate)
        elif status == SkillStatus.ACTIVE and usage >= DEMOTION_MIN_APPLICATIONS and rate < DEMOTION_MAX_SUCCESS_RATE:
            status = SkillStatus.DISABLED
            _log.warning("skill.demoted", event_type="memory", skill_id=skill_id, usage=usage, success_rate=rate)
        updated = skill.model_copy(
            update={
                "usage_count": usage,
                "success_rate": rate,
                "status": status,
                "updated_ts": datetime.now(UTC),
            }
        )
        await self.save(updated)
        return updated

    async def supersede(self, old_id: str, new_id: str) -> None:
        await self._db.conn.execute(
            "UPDATE skills SET superseded_by = ?, updated_ts = ? WHERE id = ?",
            (new_id, self._clock.now().isoformat(), old_id),
        )
        await self._db.conn.commit()

    async def new_version(self, skill: Skill, **updates: object) -> Skill:
        """Create the next version of a skill and supersede the old one."""
        new = skill.model_copy(
            update={
                "id": self._ids.execution_id(),
                "version": skill.version + 1,
                "usage_count": 0,
                "success_rate": 0.0,
                "superseded_by": None,
                **updates,
            }
        )
        await self.save(new)
        await self.supersede(skill.id, new.id)
        return new

    @staticmethod
    def _from_row(row: object) -> Skill:
        d = dict(row)  # type: ignore[call-overload]
        return Skill(
            id=d["id"],
            name=d["name"],
            description=d["description"],
            procedure_steps=tuple(json.loads(d["procedure_steps"])),
            version=d["version"],
            status=d["status"],
            success_rate=d["success_rate"],
            usage_count=d["usage_count"],
            confidence=d["confidence"],
            preferred_tools=tuple(json.loads(d["preferred_tools"])),
            known_failure_modes=tuple(json.loads(d["known_failure_modes"])),
            source_experience_ids=tuple(json.loads(d["source_experience_ids"])),
            created_ts=datetime.fromisoformat(d["created_ts"]),
            updated_ts=datetime.fromisoformat(d["updated_ts"]),
            superseded_by=d["superseded_by"],
        )
