"""Trajectory Store — durable learning from complete task execution history.

Phase 2: Stores complete trajectories (actions, observations, decisions, failures)
with fast async operations. Follows episodic.py patterns for consistency.

WHY separate from episodes: Trajectories are task-level summaries with structured
analysis fields (decision traces, failure taxonomy, experiences). Episodes are
step-level raw logs. Both are needed for different purposes.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING

from atlas.infra.clock import Clock
from atlas.infra.db import Database
from atlas.infra.ids import IdGenerator
from atlas.infra.logging import get_logger
from atlas.memory.trajectory import (
    ActionRecord,
    DecisionOutcome,
    DecisionPoint,
    DecisionTrace,
    Experience,
    ExperienceCategory,
    ExperienceQuery,
    FailureCategory,
    FailureRecord,
    ObservationRecord,
    Trajectory,
    TrajectoryQuery,
)

if TYPE_CHECKING:
    from atlas.infra.bus import MessageBus

_log = get_logger("atlas.memory.trajectory")


class TrajectoryStore:
    """Async trajectory storage with comprehensive querying.

    Performance targets:
    - Save trajectory: < 50ms (one transaction, 4 tables)
    - Query by task_id: < 10ms (indexed)
    - Query failures: < 20ms (indexed by category)
    - Query experiences: < 30ms (indexed by confidence/reuse)
    """

    def __init__(
        self,
        *,
        db: Database,
        ids: IdGenerator,
        clock: Clock,
    ) -> None:
        self._db = db
        self._ids = ids
        self._clock = clock
        self._bus: MessageBus | None = None

    def set_bus(self, bus: MessageBus) -> None:
        """Connect to event bus for trajectory events."""
        self._bus = bus
        _log.info("trajectory.bus_connected", event_type="memory")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Trajectory Operations
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def save_trajectory(self, trajectory: Trajectory) -> str:
        """Save complete trajectory with all related records.

        Returns trajectory ID. Target: < 50ms.
        """
        # Serialize complex fields
        actions_json = json.dumps([a.model_dump() for a in trajectory.actions])
        observations_json = json.dumps([o.model_dump() for o in trajectory.observations])
        decision_trace_ids = json.dumps(list(trajectory.decision_traces))
        failure_record_ids = json.dumps(list(trajectory.failure_records))
        plan_steps_json = json.dumps(list(trajectory.plan_steps))

        await self._db.conn.execute(
            """
            INSERT INTO trajectories (
                id, task_id, correlation_id, request, goal, plan_steps,
                risk_level, plan_confidence, actions, observations,
                decision_trace_ids, failure_record_ids, replan_count,
                verification_passed, verification_score, success, answer, error,
                steps_taken, latency_ms, tokens_used, cost_usd, model_calls,
                tool_calls, created_ts, completed_ts,
                atlas_version, git_commit, config_hash, strategy_id,
                strategy_version, model_version, capability_snapshot_version,
                safety_events, completion_confidence
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                trajectory.id,
                trajectory.task_id,
                trajectory.correlation_id,
                trajectory.request,
                trajectory.goal,
                plan_steps_json,
                trajectory.risk_level,
                trajectory.plan_confidence,
                actions_json,
                observations_json,
                decision_trace_ids,
                failure_record_ids,
                trajectory.replan_count,
                int(trajectory.verification_passed) if trajectory.verification_passed is not None else None,
                trajectory.verification_score,
                int(trajectory.success),
                trajectory.answer,
                trajectory.error,
                trajectory.steps_taken,
                trajectory.latency_ms,
                trajectory.tokens_used,
                trajectory.cost_usd,
                trajectory.model_calls,
                trajectory.tool_calls,
                trajectory.created_ts.isoformat(),
                trajectory.completed_ts.isoformat(),
                trajectory.atlas_version,
                trajectory.git_commit,
                trajectory.config_hash,
                trajectory.strategy_id,
                trajectory.strategy_version,
                trajectory.model_version,
                trajectory.capability_snapshot_version,
                json.dumps(list(trajectory.safety_events)),
                trajectory.completion_confidence,
            ),
        )
        await self._db.conn.commit()

        _log.info(
            "trajectory.saved",
            event_type="memory",
            trajectory_id=trajectory.id,
            task_id=trajectory.task_id,
            success=trajectory.success,
            steps=trajectory.steps_taken,
            latency_ms=trajectory.latency_ms,
        )

        # Emit event for WebSocket broadcast
        if self._bus:
            import asyncio

            from atlas.infra.bus import MemoryBusEvent

            asyncio.create_task(
                self._bus.publish(
                    "memory",
                    MemoryBusEvent(
                        correlation_id=trajectory.correlation_id,
                        task_id=trajectory.task_id,
                        kind="trajectory.saved",
                        memory_type="trajectory",
                        count=1,
                        items=[f"Trajectory {trajectory.id}: {trajectory.goal[:50]}"],
                        metadata={
                            "success": trajectory.success,
                            "steps": trajectory.steps_taken,
                            "replan_count": trajectory.replan_count,
                        },
                    ),
                )
            )

        return trajectory.id

    async def get_trajectory(self, trajectory_id: str) -> Trajectory | None:
        """Get trajectory by ID. Target: < 10ms."""
        cur = await self._db.conn.execute("SELECT * FROM trajectories WHERE id = ?", (trajectory_id,))
        row = await cur.fetchone()
        return self._trajectory_from_row(row) if row else None

    async def get_trajectory_by_task(self, task_id: str) -> Trajectory | None:
        """Get trajectory by task_id (one-to-one). Target: < 10ms."""
        cur = await self._db.conn.execute("SELECT * FROM trajectories WHERE task_id = ?", (task_id,))
        row = await cur.fetchone()
        return self._trajectory_from_row(row) if row else None

    async def query_trajectories(self, query: TrajectoryQuery) -> list[Trajectory]:
        """Query trajectories with filters. Target: < 50ms."""
        conditions = []
        params: list[str | int | float] = []

        if query.task_id:
            conditions.append("task_id = ?")
            params.append(query.task_id)

        if query.correlation_id:
            conditions.append("correlation_id = ?")
            params.append(query.correlation_id)

        if query.success is not None:
            conditions.append("success = ?")
            params.append(int(query.success))

        if query.min_replan_count > 0:
            conditions.append("replan_count >= ?")
            params.append(query.min_replan_count)

        if query.min_steps > 0:
            conditions.append("steps_taken >= ?")
            params.append(query.min_steps)

        if query.min_latency_ms > 0:
            conditions.append("latency_ms >= ?")
            params.append(query.min_latency_ms)

        if query.from_ts:
            conditions.append("completed_ts >= ?")
            params.append(query.from_ts.isoformat())

        if query.to_ts:
            conditions.append("completed_ts <= ?")
            params.append(query.to_ts.isoformat())

        where_clause = " AND ".join(conditions) if conditions else "1=1"
        params.append(query.limit)

        sql = f"""
            SELECT * FROM trajectories
            WHERE {where_clause}
            ORDER BY completed_ts DESC
            LIMIT ?
        """

        cur = await self._db.conn.execute(sql, tuple(params))
        rows = await cur.fetchall()
        return [self._trajectory_from_row(r) for r in rows]

    async def get_recent_trajectories(self, limit: int = 20) -> list[Trajectory]:
        """Get most recent trajectories. Target: < 20ms."""
        cur = await self._db.conn.execute("SELECT * FROM trajectories ORDER BY completed_ts DESC LIMIT ?", (limit,))
        rows = await cur.fetchall()
        return [self._trajectory_from_row(r) for r in rows]

    async def get_failed_trajectories(self, limit: int = 50) -> list[Trajectory]:
        """Get failed tasks for analysis. Target: < 20ms."""
        cur = await self._db.conn.execute(
            """
            SELECT * FROM trajectories
            WHERE success = 0
            ORDER BY completed_ts DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cur.fetchall()
        return [self._trajectory_from_row(r) for r in rows]

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Decision Trace Operations
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def save_decision_trace(self, trace: DecisionTrace) -> str:
        """Save decision trace. Target: < 10ms."""
        options_json = json.dumps(list(trace.options_considered))
        context_json = json.dumps(trace.context)

        await self._db.conn.execute(
            """
            INSERT INTO decision_traces (
                id, task_id, correlation_id, ts, decision_point,
                options_considered, chosen_option, rationale, context_json,
                outcome, outcome_detail, confidence, latency_ms, cost_usd
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                trace.id,
                trace.task_id,
                trace.correlation_id,
                trace.ts.isoformat(),
                trace.decision_point.value,
                options_json,
                trace.chosen_option,
                trace.rationale,
                context_json,
                trace.outcome.value,
                trace.outcome_detail,
                trace.confidence,
                trace.latency_ms,
                trace.cost_usd,
            ),
        )
        await self._db.conn.commit()

        _log.debug(
            "decision_trace.saved",
            event_type="memory",
            trace_id=trace.id,
            decision_point=trace.decision_point.value,
            chosen=trace.chosen_option,
        )

        return trace.id

    async def get_decision_traces(
        self,
        task_id: str | None = None,
        decision_point: DecisionPoint | None = None,
        outcome: DecisionOutcome | None = None,
        limit: int = 100,
    ) -> list[DecisionTrace]:
        """Query decision traces. Target: < 20ms."""
        conditions = []
        params: list[str | int] = []

        if task_id:
            conditions.append("task_id = ?")
            params.append(task_id)

        if decision_point:
            conditions.append("decision_point = ?")
            params.append(decision_point.value)

        if outcome:
            conditions.append("outcome = ?")
            params.append(outcome.value)

        where_clause = " AND ".join(conditions) if conditions else "1=1"
        params.append(limit)

        sql = f"""
            SELECT * FROM decision_traces
            WHERE {where_clause}
            ORDER BY ts DESC
            LIMIT ?
        """

        cur = await self._db.conn.execute(sql, tuple(params))
        rows = await cur.fetchall()
        return [self._decision_trace_from_row(r) for r in rows]

    async def update_decision_outcome(
        self,
        trace_id: str,
        outcome: DecisionOutcome,
        outcome_detail: str | None = None,
    ) -> None:
        """Update decision outcome after observing result. Target: < 5ms."""
        await self._db.conn.execute(
            """
            UPDATE decision_traces
            SET outcome = ?, outcome_detail = ?
            WHERE id = ?
            """,
            (outcome.value, outcome_detail, trace_id),
        )
        await self._db.conn.commit()

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Failure Record Operations
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def save_failure_record(self, failure: FailureRecord) -> str:
        """Save failure record for taxonomy building. Target: < 10ms."""
        context_json = json.dumps(failure.context)
        similar_ids_json = json.dumps(list(failure.similar_failure_ids))

        await self._db.conn.execute(
            """
            INSERT INTO failure_records (
                id, task_id, correlation_id, ts, category, step, component,
                error_message, context_json, recovered, recovery_method,
                recovery_succeeded, similar_failure_ids, mitigation_suggested,
                mitigation_applied
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                failure.id,
                failure.task_id,
                failure.correlation_id,
                failure.ts.isoformat(),
                failure.category.value,
                failure.step,
                failure.component,
                failure.error_message,
                context_json,
                int(failure.recovered),
                failure.recovery_method,
                int(failure.recovery_succeeded),
                similar_ids_json,
                failure.mitigation_suggested,
                int(failure.mitigation_applied),
            ),
        )
        await self._db.conn.commit()

        _log.info(
            "failure.recorded",
            event_type="memory",
            failure_id=failure.id,
            category=failure.category.value,
            component=failure.component,
            recovered=failure.recovered,
        )

        return failure.id

    async def get_failure_records(
        self,
        task_id: str | None = None,
        category: FailureCategory | None = None,
        component: str | None = None,
        recovered_only: bool = False,
        limit: int = 100,
    ) -> list[FailureRecord]:
        """Query failure records. Target: < 20ms."""
        conditions = []
        params: list[str | int] = []

        if task_id:
            conditions.append("task_id = ?")
            params.append(task_id)

        if category:
            conditions.append("category = ?")
            params.append(category.value)

        if component:
            conditions.append("component = ?")
            params.append(component)

        if recovered_only:
            conditions.append("recovered = 1 AND recovery_succeeded = 1")

        where_clause = " AND ".join(conditions) if conditions else "1=1"
        params.append(limit)

        sql = f"""
            SELECT * FROM failure_records
            WHERE {where_clause}
            ORDER BY ts DESC
            LIMIT ?
        """

        cur = await self._db.conn.execute(sql, tuple(params))
        rows = await cur.fetchall()
        return [self._failure_record_from_row(r) for r in rows]

    async def get_failure_patterns(
        self,
        category: FailureCategory,
        min_occurrences: int = 3,
    ) -> list[dict[str, object]]:
        """Identify recurring failure patterns. Target: < 50ms."""
        cur = await self._db.conn.execute(
            """
            SELECT category, component, COUNT(*) as occurrence_count,
                   SUM(recovered) as recovery_count,
                   SUM(recovery_succeeded) as recovery_success_count
            FROM failure_records
            WHERE category = ?
            GROUP BY category, component
            HAVING occurrence_count >= ?
            ORDER BY occurrence_count DESC
            """,
            (category.value, min_occurrences),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Experience Operations
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def save_experience(self, experience: Experience) -> str:
        """Save extracted experience. Target: < 10ms."""
        supporting_actions_json = json.dumps(list(experience.supporting_actions))
        supporting_observations_json = json.dumps(list(experience.supporting_observations))
        counter_examples_json = json.dumps(list(experience.counter_examples))

        await self._db.conn.execute(
            """
            INSERT INTO experiences (
                id, trajectory_id, task_id, correlation_id, category,
                lesson_text, applicability_context, confidence,
                supporting_actions, supporting_observations, counter_examples,
                reuse_count, success_rate, avg_improvement_ms, avg_cost_savings_usd,
                extracted_ts, last_applied_ts, superseded_by
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                experience.id,
                experience.trajectory_id,
                experience.task_id,
                experience.correlation_id,
                experience.category.value,
                experience.lesson_text,
                experience.applicability_context,
                experience.confidence,
                supporting_actions_json,
                supporting_observations_json,
                counter_examples_json,
                experience.reuse_count,
                experience.success_rate,
                experience.avg_improvement_ms,
                experience.avg_cost_savings_usd,
                experience.extracted_ts.isoformat(),
                experience.last_applied_ts.isoformat() if experience.last_applied_ts else None,
                experience.superseded_by,
            ),
        )
        await self._db.conn.commit()

        _log.info(
            "experience.saved",
            event_type="memory",
            experience_id=experience.id,
            category=experience.category.value,
            confidence=experience.confidence,
        )

        return experience.id

    async def get_experience(self, experience_id: str) -> Experience | None:
        """Get experience by ID. Target: < 5ms."""
        cur = await self._db.conn.execute("SELECT * FROM experiences WHERE id = ?", (experience_id,))
        row = await cur.fetchone()
        return self._experience_from_row(row) if row else None

    async def query_experiences(self, query: ExperienceQuery) -> list[Experience]:
        """Query experiences with filters. Target: < 30ms."""
        conditions = ["superseded_by IS NULL"]  # Exclude superseded
        params: list[str | int | float] = []

        if query.category:
            conditions.append("category = ?")
            params.append(query.category.value)

        if query.min_confidence > 0:
            conditions.append("confidence >= ?")
            params.append(query.min_confidence)

        if query.min_reuse_count > 0:
            conditions.append("reuse_count >= ?")
            params.append(query.min_reuse_count)

        if query.min_success_rate > 0:
            conditions.append("success_rate >= ?")
            params.append(query.min_success_rate)

        if query.applicability_context:
            conditions.append("applicability_context LIKE ?")
            params.append(f"%{query.applicability_context}%")

        where_clause = " AND ".join(conditions)
        params.append(query.limit)

        sql = f"""
            SELECT * FROM experiences
            WHERE {where_clause}
            ORDER BY confidence DESC, reuse_count DESC
            LIMIT ?
        """

        cur = await self._db.conn.execute(sql, tuple(params))
        rows = await cur.fetchall()
        return [self._experience_from_row(r) for r in rows]

    async def record_experience_application(
        self,
        experience_id: str,
        task_id: str,
        success: bool,
        improvement_ms: int | None = None,
        cost_savings_usd: float | None = None,
    ) -> None:
        """Record that an experience was applied. Target: < 10ms."""
        # Insert application record
        await self._db.conn.execute(
            """
            INSERT INTO experience_applications (
                experience_id, task_id, applied_ts, success,
                improvement_ms, cost_savings_usd
            ) VALUES (?,?,?,?,?,?)
            """,
            (
                experience_id,
                task_id,
                self._clock.now().isoformat(),
                int(success),
                improvement_ms,
                cost_savings_usd,
            ),
        )

        # Update experience stats
        await self._db.conn.execute(
            """
            UPDATE experiences
            SET reuse_count = reuse_count + 1,
                last_applied_ts = ?,
                success_rate = (
                    SELECT CAST(SUM(success) AS REAL) / COUNT(*)
                    FROM experience_applications
                    WHERE experience_id = ?
                ),
                avg_improvement_ms = (
                    SELECT AVG(improvement_ms)
                    FROM experience_applications
                    WHERE experience_id = ? AND improvement_ms IS NOT NULL
                ),
                avg_cost_savings_usd = (
                    SELECT AVG(cost_savings_usd)
                    FROM experience_applications
                    WHERE experience_id = ? AND cost_savings_usd IS NOT NULL
                )
            WHERE id = ?
            """,
            (
                self._clock.now().isoformat(),
                experience_id,
                experience_id,
                experience_id,
                experience_id,
            ),
        )
        await self._db.conn.commit()

    async def supersede_experience(
        self,
        old_experience_id: str,
        new_experience_id: str,
    ) -> None:
        """Mark an experience as superseded by a better one. Target: < 5ms."""
        await self._db.conn.execute(
            "UPDATE experiences SET superseded_by = ? WHERE id = ?",
            (new_experience_id, old_experience_id),
        )
        await self._db.conn.commit()

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Row Deserializers
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    @staticmethod
    def _trajectory_from_row(row: object) -> Trajectory:
        d = dict(row)  # type: ignore[call-overload]

        # Deserialize JSON fields
        actions = tuple(ActionRecord(**a) for a in json.loads(d["actions"]))
        observations = tuple(ObservationRecord(**o) for o in json.loads(d["observations"]))
        decision_traces = tuple(json.loads(d["decision_trace_ids"]))
        failure_records = tuple(json.loads(d["failure_record_ids"]))
        plan_steps = tuple(json.loads(d["plan_steps"]))

        return Trajectory(
            id=d["id"],
            task_id=d["task_id"],
            correlation_id=d["correlation_id"],
            request=d["request"],
            goal=d["goal"],
            plan_steps=plan_steps,
            risk_level=d["risk_level"],
            plan_confidence=d["plan_confidence"],
            actions=actions,
            observations=observations,
            decision_traces=decision_traces,
            failure_records=failure_records,
            replan_count=d["replan_count"],
            verification_passed=bool(d["verification_passed"]) if d["verification_passed"] is not None else None,
            verification_score=d["verification_score"],
            success=bool(d["success"]),
            answer=d["answer"],
            error=d["error"],
            steps_taken=d["steps_taken"],
            latency_ms=d["latency_ms"],
            tokens_used=d["tokens_used"],
            cost_usd=d["cost_usd"],
            model_calls=d["model_calls"],
            tool_calls=d["tool_calls"],
            created_ts=datetime.fromisoformat(d["created_ts"]),
            completed_ts=datetime.fromisoformat(d["completed_ts"]),
            atlas_version=d.get("atlas_version"),
            git_commit=d.get("git_commit"),
            config_hash=d.get("config_hash"),
            strategy_id=d.get("strategy_id"),
            strategy_version=d.get("strategy_version"),
            model_version=d.get("model_version"),
            capability_snapshot_version=d.get("capability_snapshot_version"),
            safety_events=tuple(json.loads(d.get("safety_events") or "[]")),
            completion_confidence=d.get("completion_confidence"),
        )

    @staticmethod
    def _decision_trace_from_row(row: object) -> DecisionTrace:
        d = dict(row)  # type: ignore[call-overload]
        return DecisionTrace(
            id=d["id"],
            task_id=d["task_id"],
            correlation_id=d["correlation_id"],
            ts=datetime.fromisoformat(d["ts"]),
            decision_point=DecisionPoint(d["decision_point"]),
            options_considered=tuple(json.loads(d["options_considered"])),
            chosen_option=d["chosen_option"],
            rationale=d["rationale"],
            context=json.loads(d["context_json"]),
            outcome=DecisionOutcome(d["outcome"]),
            outcome_detail=d["outcome_detail"],
            confidence=d["confidence"],
            latency_ms=d["latency_ms"],
            cost_usd=d["cost_usd"],
        )

    @staticmethod
    def _failure_record_from_row(row: object) -> FailureRecord:
        d = dict(row)  # type: ignore[call-overload]
        return FailureRecord(
            id=d["id"],
            task_id=d["task_id"],
            correlation_id=d["correlation_id"],
            ts=datetime.fromisoformat(d["ts"]),
            category=FailureCategory(d["category"]),
            step=d["step"],
            component=d["component"],
            error_message=d["error_message"],
            context=json.loads(d["context_json"]),
            recovered=bool(d["recovered"]),
            recovery_method=d["recovery_method"],
            recovery_succeeded=bool(d["recovery_succeeded"]),
            similar_failure_ids=tuple(json.loads(d["similar_failure_ids"])),
            mitigation_suggested=d["mitigation_suggested"],
            mitigation_applied=bool(d["mitigation_applied"]),
        )

    @staticmethod
    def _experience_from_row(row: object) -> Experience:
        d = dict(row)  # type: ignore[call-overload]
        return Experience(
            id=d["id"],
            trajectory_id=d["trajectory_id"],
            task_id=d["task_id"],
            correlation_id=d["correlation_id"],
            category=ExperienceCategory(d["category"]),
            lesson_text=d["lesson_text"],
            applicability_context=d["applicability_context"],
            confidence=d["confidence"],
            supporting_actions=tuple(json.loads(d["supporting_actions"])),
            supporting_observations=tuple(json.loads(d["supporting_observations"])),
            counter_examples=tuple(json.loads(d["counter_examples"])),
            reuse_count=d["reuse_count"],
            success_rate=d["success_rate"],
            avg_improvement_ms=d["avg_improvement_ms"],
            avg_cost_savings_usd=d["avg_cost_savings_usd"],
            extracted_ts=datetime.fromisoformat(d["extracted_ts"]),
            last_applied_ts=datetime.fromisoformat(d["last_applied_ts"]) if d["last_applied_ts"] else None,
            superseded_by=d["superseded_by"],
        )
