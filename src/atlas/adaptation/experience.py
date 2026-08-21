"""Structured experience extraction and validation (Prompt 4 §9-§10).

Extraction is DETERMINISTIC — it reads what actually happened from the
trajectory; it never stores an LLM-generated lesson as fact (§9: "Do not
permanently store an LLM-generated lesson as fact without evidence").

Validation (§10): an experience becomes reusable only when evidence is
sufficient, the pattern repeats, success is measurable and task similarity
is meaningful. One successful task never becomes a permanent skill/strategy.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from atlas.adaptation.domain import MIN_EVIDENCE_DEFAULT
from atlas.infra.clock import Clock, SystemClock
from atlas.infra.db import Database
from atlas.infra.logging import get_logger
from atlas.memory.trajectory import FailureRecord, Trajectory

_log = get_logger("atlas.adaptation.experience")

# §10 evidence thresholds
VALIDATION_MIN_PATTERN_REPEAT = MIN_EVIDENCE_DEFAULT  # pattern must repeat >=3 times
VALIDATION_MIN_SUCCESS_RATE = 0.7  # measurable success when applied


class StructuredExperience(BaseModel):
    """§9: what happened, structured — not a prose lesson."""

    model_config = ConfigDict(frozen=True)

    experience_id: str = Field(default_factory=lambda: f"sx_{uuid.uuid4().hex[:12]}")
    trajectory_id: str
    problem_pattern: str
    what_worked: str = ""
    what_failed: str = ""
    successful_action_sequence: tuple[str, ...] = ()
    failed_action_sequence: tuple[str, ...] = ()
    recovery_pattern: str = ""
    useful_evidence: tuple[str, ...] = ()
    lesson_candidate: str = ""  # a CANDIDATE only — never stored as fact
    validated: bool = False
    created_ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class ExperienceExtractor:
    """Trajectory → StructuredExperience, fully deterministic."""

    def __init__(self, *, clock: Clock | None = None) -> None:
        self._clock = clock or SystemClock()

    def extract(
        self,
        trajectory: Trajectory,
        failure_records: tuple[FailureRecord, ...] = (),
    ) -> StructuredExperience:
        ok_by_step = {o.step: o.ok for o in trajectory.observations}
        successful: list[str] = []
        failed: list[str] = []
        for action in trajectory.actions:
            label = action.tool or action.kind
            if ok_by_step.get(action.step, True):
                successful.append(f"{action.step}:{label}")
            else:
                failed.append(f"{action.step}:{label}")

        recovered = [r for r in failure_records if r.recovered and r.recovery_succeeded]
        recovery_pattern = recovered[-1].recovery_method or "" if recovered else ""

        evidence = tuple(f"obs:{o.step}:{(o.content or '')[:80]}" for o in trajectory.observations if o.ok)[:8]

        what_worked = "; ".join(successful[:6]) if trajectory.success else ""
        what_failed = trajectory.error or ("; ".join(failed[:6]) if failed else "")

        # Deterministic lesson CANDIDATE — phrased as hypothesis, not fact.
        if trajectory.success and successful:
            lesson_candidate = f"for '{trajectory.goal[:80]}', the sequence {' → '.join(successful[:4])} succeeded"
        elif what_failed:
            lesson_candidate = f"for '{trajectory.goal[:80]}', avoid: {what_failed[:120]}"
        else:
            lesson_candidate = ""

        experience = StructuredExperience(
            trajectory_id=trajectory.id,
            problem_pattern=_problem_pattern(trajectory),
            what_worked=what_worked,
            what_failed=what_failed,
            successful_action_sequence=tuple(successful),
            failed_action_sequence=tuple(failed),
            recovery_pattern=recovery_pattern,
            useful_evidence=evidence,
            lesson_candidate=lesson_candidate,
            created_ts=self._clock.now().isoformat(),
        )
        _log.info(
            "experience.extracted",
            event_type="adaptation",
            trajectory_id=trajectory.id,
            pattern=experience.problem_pattern,
        )
        return experience


def _problem_pattern(trajectory: Trajectory) -> str:
    """Deterministic, coarse problem key so repeats are comparable."""
    head = " ".join(trajectory.goal.strip().lower().split()[:4])
    return f"{head}|risk={trajectory.risk_level}"


class ValidationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    valid: bool
    reasons: tuple[str, ...] = ()


class ExperienceValidator:
    """§10 evidence-gate: reusable only with repeated, measurable success."""

    def validate_pattern(
        self,
        experiences: tuple[StructuredExperience, ...],
        *,
        application_successes: int = 0,
        application_attempts: int = 0,
    ) -> ValidationResult:
        reasons: list[str] = []
        if len(experiences) < VALIDATION_MIN_PATTERN_REPEAT:
            reasons.append(f"pattern repeated {len(experiences)}x < {VALIDATION_MIN_PATTERN_REPEAT}")
        patterns = {e.problem_pattern for e in experiences}
        if len(patterns) > 1:
            reasons.append("task similarity not meaningful (mixed patterns)")
        if application_attempts > 0:
            rate = application_successes / application_attempts
            if rate < VALIDATION_MIN_SUCCESS_RATE:
                reasons.append(f"success rate {rate:.2f} < {VALIDATION_MIN_SUCCESS_RATE}")
        elif experiences and not any(e.what_worked for e in experiences):
            reasons.append("no measurable success evidence")
        return ValidationResult(valid=not reasons, reasons=tuple(reasons))


class ExperienceStore:
    """Persists structured experiences (migration 016)."""

    def __init__(self, *, db: Database, clock: Clock | None = None) -> None:
        self._db = db
        self._clock = clock or SystemClock()

    async def save(self, experience: StructuredExperience) -> None:
        await self._db.conn.execute(
            """
            INSERT OR REPLACE INTO structured_experiences (
                experience_id, trajectory_id, problem_pattern, what_worked,
                what_failed, successful_actions_json, failed_actions_json,
                recovery_pattern, useful_evidence_json, lesson_candidate,
                validated, created_ts
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                experience.experience_id,
                experience.trajectory_id,
                experience.problem_pattern,
                experience.what_worked,
                experience.what_failed,
                json.dumps(list(experience.successful_action_sequence)),
                json.dumps(list(experience.failed_action_sequence)),
                experience.recovery_pattern,
                json.dumps(list(experience.useful_evidence)),
                experience.lesson_candidate,
                int(experience.validated),
                experience.created_ts,
            ),
        )
        await self._db.conn.commit()

    async def for_pattern(self, problem_pattern: str) -> tuple[StructuredExperience, ...]:
        cur = await self._db.conn.execute(
            "SELECT * FROM structured_experiences WHERE problem_pattern=? ORDER BY created_ts",
            (problem_pattern,),
        )
        rows = await cur.fetchall()
        return tuple(_from_row(r) for r in rows)

    async def mark_validated(self, experience_id: str) -> None:
        await self._db.conn.execute(
            "UPDATE structured_experiences SET validated=1 WHERE experience_id=?",
            (experience_id,),
        )
        await self._db.conn.commit()


def _from_row(row: object) -> StructuredExperience:
    d = dict(row)  # type: ignore[call-overload]
    return StructuredExperience(
        experience_id=d["experience_id"],
        trajectory_id=d["trajectory_id"],
        problem_pattern=d["problem_pattern"],
        what_worked=d["what_worked"],
        what_failed=d["what_failed"],
        successful_action_sequence=tuple(json.loads(d["successful_actions_json"])),
        failed_action_sequence=tuple(json.loads(d["failed_actions_json"])),
        recovery_pattern=d["recovery_pattern"],
        useful_evidence=tuple(json.loads(d["useful_evidence_json"])),
        lesson_candidate=d["lesson_candidate"],
        validated=bool(d["validated"]),
        created_ts=d["created_ts"],
    )


class SkillState(StrEnum):
    """§11 skill lifecycle."""

    EXPERIMENTAL = "EXPERIMENTAL"
    VALIDATED = "VALIDATED"
    ACTIVE = "ACTIVE"
    DEPRECATED = "DEPRECATED"


# Evidence gates between states (§11: repeated pattern, successful outcome,
# stable procedure, measurable benefit)
VALIDATED_MIN_APPLICATIONS = 3
VALIDATED_MIN_SUCCESS_RATE = 0.7
DEPRECATE_MIN_APPLICATIONS = 5
DEPRECATE_MAX_SUCCESS_RATE = 0.3

_ALLOWED_TRANSITIONS: dict[SkillState, tuple[SkillState, ...]] = {
    SkillState.EXPERIMENTAL: (SkillState.VALIDATED, SkillState.DEPRECATED),
    SkillState.VALIDATED: (SkillState.ACTIVE, SkillState.DEPRECATED),
    SkillState.ACTIVE: (SkillState.DEPRECATED,),
    SkillState.DEPRECATED: (),
}


class SkillLifecycleStore:
    """§11 state machine per skill: EXPERIMENTAL → VALIDATED → ACTIVE →
    DEPRECATED, with evidence gates on every transition."""

    def __init__(self, *, db: Database, clock: Clock | None = None) -> None:
        self._db = db
        self._clock = clock or SystemClock()

    async def ensure(self, skill_name: str) -> SkillState:
        cur = await self._db.conn.execute("SELECT state FROM skill_lifecycle WHERE skill_name=?", (skill_name,))
        row = await cur.fetchone()
        if row is None:
            await self._db.conn.execute(
                "INSERT INTO skill_lifecycle (skill_name, state, applications, successes, reason, updated_ts)"
                " VALUES (?, 'EXPERIMENTAL', 0, 0, '', ?)",
                (skill_name, self._clock.now().isoformat()),
            )
            await self._db.conn.commit()
            return SkillState.EXPERIMENTAL
        return SkillState(row["state"])

    async def record_application(self, skill_name: str, *, success: bool) -> None:
        await self.ensure(skill_name)
        await self._db.conn.execute(
            "UPDATE skill_lifecycle SET applications=applications+1,"
            " successes=successes+?, updated_ts=? WHERE skill_name=?",
            (int(success), self._clock.now().isoformat(), skill_name),
        )
        await self._db.conn.commit()

    async def state(self, skill_name: str) -> tuple[SkillState, int, int]:
        await self.ensure(skill_name)
        cur = await self._db.conn.execute(
            "SELECT state, applications, successes FROM skill_lifecycle WHERE skill_name=?",
            (skill_name,),
        )
        row = await cur.fetchone()
        assert row is not None
        return SkillState(row["state"]), row["applications"], row["successes"]

    async def transition(self, skill_name: str, target: SkillState, *, reason: str = "") -> bool:
        """Evidence-gated transition; returns False when the gate refuses."""
        state, applications, successes = await self.state(skill_name)
        if target not in _ALLOWED_TRANSITIONS[state]:
            return False
        rate = successes / applications if applications else 0.0
        if target is SkillState.VALIDATED and not (
            applications >= VALIDATED_MIN_APPLICATIONS and rate >= VALIDATED_MIN_SUCCESS_RATE
        ):
            return False
        if target is SkillState.ACTIVE and state is not SkillState.VALIDATED:
            return False
        if (
            target is SkillState.DEPRECATED
            and state is SkillState.ACTIVE
            and not (applications >= DEPRECATE_MIN_APPLICATIONS and rate <= DEPRECATE_MAX_SUCCESS_RATE)
        ):
            return False
        await self._db.conn.execute(
            "UPDATE skill_lifecycle SET state=?, reason=?, updated_ts=? WHERE skill_name=?",
            (target.value, reason, self._clock.now().isoformat(), skill_name),
        )
        await self._db.conn.commit()
        _log.info(
            "skill.transitioned",
            event_type="adaptation",
            skill=skill_name,
            state=target.value,
            reason=reason,
        )
        return True


__all__ = [
    "VALIDATION_MIN_PATTERN_REPEAT",
    "VALIDATION_MIN_SUCCESS_RATE",
    "ExperienceExtractor",
    "ExperienceStore",
    "ExperienceValidator",
    "SkillLifecycleStore",
    "SkillState",
    "StructuredExperience",
    "ValidationResult",
]
