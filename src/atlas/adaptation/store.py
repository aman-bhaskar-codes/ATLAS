"""Durable store for adaptation artifacts (migration 015 tables).

Persists the artifacts the evaluation/analyzer layer produces: classified
failures, causal analyses, trajectory evaluations, outcome verdicts,
adaptation events (§74 audit trail) and negative experiences (§89).
Same single-connection Database as every other store; JSON columns are used
ONLY for typed-model serialization, never for ad-hoc blobs.
"""

from __future__ import annotations

import json

from atlas.adaptation.domain import (
    EvaluationVerdict,
    FailureAnalysis,
    OutcomeEvaluation,
    TrajectoryEvaluation,
)
from atlas.adaptation.taxonomy import FailureClass, FailureTaxonomy
from atlas.infra.clock import Clock, SystemClock
from atlas.infra.db import Database
from atlas.infra.logging import get_logger

_log = get_logger("atlas.adaptation.store")


class AdaptationStore:
    def __init__(self, *, db: Database, clock: Clock | None = None) -> None:
        self._db = db
        self._clock = clock or SystemClock()

    # ── failure taxonomy ────────────────────────────────────────────────

    async def save_failure(self, failure: FailureTaxonomy) -> None:
        await self._db.conn.execute(
            """
            INSERT OR REPLACE INTO failure_taxonomy (
                failure_id, trajectory_id, failure_class, step_id, evidence_json,
                root_cause_candidate, recoverable, recovery_attempts,
                final_resolution, created_ts
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                failure.failure_id,
                failure.trajectory_id,
                failure.failure_class.value,
                failure.step_id,
                json.dumps(list(failure.evidence)),
                int(failure.root_cause_candidate),
                int(failure.recoverable),
                failure.recovery_attempts,
                failure.final_resolution,
                failure.created_ts,
            ),
        )
        await self._db.conn.commit()

    async def failures_for_trajectory(self, trajectory_id: str) -> tuple[FailureTaxonomy, ...]:
        cur = await self._db.conn.execute(
            "SELECT * FROM failure_taxonomy WHERE trajectory_id=? ORDER BY created_ts",
            (trajectory_id,),
        )
        rows = await cur.fetchall()
        return tuple(self._failure_from_row(r) for r in rows)

    async def recent_failures(
        self, failure_class: FailureClass | None = None, *, limit: int = 200
    ) -> tuple[FailureTaxonomy, ...]:
        if failure_class is not None:
            cur = await self._db.conn.execute(
                "SELECT * FROM failure_taxonomy WHERE failure_class=? ORDER BY created_ts DESC LIMIT ?",
                (failure_class.value, limit),
            )
        else:
            cur = await self._db.conn.execute(
                "SELECT * FROM failure_taxonomy ORDER BY created_ts DESC LIMIT ?", (limit,)
            )
        rows = await cur.fetchall()
        return tuple(self._failure_from_row(r) for r in rows)

    @staticmethod
    def _failure_from_row(row: object) -> FailureTaxonomy:
        d = dict(row)  # type: ignore[call-overload]
        return FailureTaxonomy(
            failure_id=d["failure_id"],
            trajectory_id=d["trajectory_id"],
            failure_class=FailureClass(d["failure_class"]),
            step_id=d["step_id"],
            evidence=tuple(json.loads(d["evidence_json"])),
            root_cause_candidate=bool(d["root_cause_candidate"]),
            recoverable=bool(d["recoverable"]),
            recovery_attempts=d["recovery_attempts"],
            final_resolution=d["final_resolution"],
            created_ts=d["created_ts"],
        )

    # ── failure analyses ────────────────────────────────────────────────

    async def save_analysis(self, analysis: FailureAnalysis) -> None:
        await self._db.conn.execute(
            """
            INSERT OR REPLACE INTO failure_analyses (
                trajectory_id, primary_cause, secondary_causes_json, evidence_json,
                confidence, avoidable, recommended_intervention, created_ts
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                analysis.trajectory_id,
                analysis.primary_cause.value,
                json.dumps([c.value for c in analysis.secondary_causes]),
                json.dumps(list(analysis.evidence)),
                analysis.confidence,
                int(analysis.avoidable),
                analysis.recommended_intervention,
                analysis.created_ts,
            ),
        )
        await self._db.conn.commit()

    async def get_analysis(self, trajectory_id: str) -> FailureAnalysis | None:
        cur = await self._db.conn.execute("SELECT * FROM failure_analyses WHERE trajectory_id=?", (trajectory_id,))
        row = await cur.fetchone()
        if row is None:
            return None
        d = dict(row)
        return FailureAnalysis(
            trajectory_id=d["trajectory_id"],
            primary_cause=FailureClass(d["primary_cause"]),
            secondary_causes=tuple(FailureClass(c) for c in json.loads(d["secondary_causes_json"])),
            evidence=tuple(json.loads(d["evidence_json"])),
            confidence=d["confidence"],
            avoidable=bool(d["avoidable"]),
            recommended_intervention=d["recommended_intervention"],
            created_ts=d["created_ts"],
        )

    # ── evaluations ─────────────────────────────────────────────────────

    async def save_evaluation(self, outcome: OutcomeEvaluation) -> None:
        dimensions = outcome.dimensions
        scored = dimensions.scores()
        await self._db.conn.execute(
            """
            INSERT OR REPLACE INTO trajectory_evaluations (
                trajectory_id, scores_json, evaluator_levels_json, created_ts
            ) VALUES (?,?,?,?)
            """,
            (
                dimensions.trajectory_id,
                json.dumps(scored),
                json.dumps(list(dimensions.evaluator_levels)),
                dimensions.created_ts,
            ),
        )
        await self._db.conn.execute(
            """
            INSERT OR REPLACE INTO outcome_evaluations (
                trajectory_id, verdict, overall_score, rationale, created_ts
            ) VALUES (?,?,?,?,?)
            """,
            (
                outcome.trajectory_id,
                outcome.verdict.value,
                outcome.overall_score,
                outcome.rationale,
                outcome.created_ts,
            ),
        )
        await self._db.conn.commit()

    async def get_evaluation(self, trajectory_id: str) -> OutcomeEvaluation | None:
        cur = await self._db.conn.execute("SELECT * FROM outcome_evaluations WHERE trajectory_id=?", (trajectory_id,))
        row = await cur.fetchone()
        if row is None:
            return None
        d = dict(row)
        cur2 = await self._db.conn.execute(
            "SELECT * FROM trajectory_evaluations WHERE trajectory_id=?", (trajectory_id,)
        )
        dim_row = await cur2.fetchone()
        scores: dict[str, float] = {}
        levels: tuple[int, ...] = ()
        dim_ts = d["created_ts"]
        if dim_row is not None:
            dd = dict(dim_row)
            scores = json.loads(dd["scores_json"])
            levels = tuple(json.loads(dd["evaluator_levels_json"]))
            dim_ts = dd["created_ts"]
        dimensions = TrajectoryEvaluation(
            trajectory_id=trajectory_id,
            evaluator_levels=levels,
            created_ts=dim_ts,
            **{name: scores.get(name) for name in scores},
        )
        return OutcomeEvaluation(
            trajectory_id=d["trajectory_id"],
            verdict=EvaluationVerdict(d["verdict"]),
            overall_score=d["overall_score"],
            dimensions=dimensions,
            rationale=d["rationale"],
            created_ts=d["created_ts"],
        )

    # ── adaptation audit trail (§74) & negative experiences (§89) ───────

    async def record_event(self, kind: str, *, ref_id: str = "", detail: dict[str, object] | None = None) -> None:
        await self._db.conn.execute(
            "INSERT INTO adaptation_events (ts, kind, ref_id, detail_json) VALUES (?,?,?,?)",
            (self._clock.now().isoformat(), kind, ref_id, json.dumps(detail or {})),
        )
        await self._db.conn.commit()

    async def recent_events(self, *, limit: int = 100) -> tuple[tuple[str, str, str, str], ...]:
        cur = await self._db.conn.execute(
            "SELECT ts, kind, ref_id, detail_json FROM adaptation_events ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        rows = await cur.fetchall()
        return tuple((r["ts"], r["kind"], r["ref_id"], r["detail_json"]) for r in rows)

    async def save_negative_experience(self, trajectory_id: str, lesson: str, *, why_rejected: str = "") -> None:
        await self._db.conn.execute(
            "INSERT INTO negative_experiences (trajectory_id, lesson, why_rejected, created_ts) VALUES (?,?,?,?)",
            (trajectory_id, lesson, why_rejected, self._clock.now().isoformat()),
        )
        await self._db.conn.commit()

    async def negative_experiences(self, *, limit: int = 100) -> tuple[tuple[str, str, str], ...]:
        cur = await self._db.conn.execute(
            "SELECT trajectory_id, lesson, why_rejected FROM negative_experiences ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        rows = await cur.fetchall()
        return tuple((r["trajectory_id"], r["lesson"], r["why_rejected"]) for r in rows)


__all__ = ["AdaptationStore"]
