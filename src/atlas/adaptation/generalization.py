"""Generalization gate, recovery evaluation, long-horizon evaluation
(Prompt 4 §38-§39, §41-§42).

§38-§39: a change that improves only the benchmark is NOT promotable — the
gate requires unseen performance to hold. §41: intelligent recovery can beat
fragile success, so recovery is scored on its own. §42: long multi-step tasks
are measured for goal completion, error accumulation and plan drift.
Everything is deterministic and persisted as evidence.
"""

from __future__ import annotations

import json

from atlas.adaptation.domain import (
    GeneralizationReport,
    LongHorizonResult,
    RecoveryEvaluation,
)
from atlas.infra.clock import Clock, SystemClock
from atlas.infra.db import Database
from atlas.infra.logging import get_logger
from atlas.memory.trajectory import DecisionOutcome, DecisionPoint, DecisionTrace, FailureRecord, Trajectory

_log = get_logger("atlas.adaptation.generalization")

#: §39: unseen score below this fraction of the in-domain score = collapse.
UNSEEN_COLLAPSE_RATIO = 0.5

#: §39: absolute floor — an unseen score this low is never promotable.
MIN_UNSEEN_ABSOLUTE = 0.1

#: §42: minimum steps for a trajectory to count as long-horizon.
MIN_LONG_HORIZON_STEPS = 5

_OUTCOME_SCORE: dict[DecisionOutcome, float] = {
    DecisionOutcome.SUCCESS: 1.0,
    DecisionOutcome.SUBOPTIMAL: 0.5,
    DecisionOutcome.FAILURE: 0.0,
    DecisionOutcome.UNKNOWN: 0.5,
}


class GeneralizationGate:
    """§39: detects benchmark-only improvements before promotion."""

    def __init__(self, *, db: Database, clock: Clock | None = None) -> None:
        self._db = db
        self._clock = clock or SystemClock()

    async def assess(
        self,
        experiment_id: str,
        *,
        in_domain: float,
        unseen: float,
        transfer: float | None = None,
        robustness: float | None = None,
    ) -> GeneralizationReport:
        reasons: list[str] = []
        if unseen < in_domain * UNSEEN_COLLAPSE_RATIO:
            reasons.append(f"unseen performance collapses relative to benchmark ({unseen:.2f} vs {in_domain:.2f}, §39)")
        if unseen < MIN_UNSEEN_ABSOLUTE:
            reasons.append(f"unseen score {unseen:.2f} below absolute floor {MIN_UNSEEN_ABSOLUTE} (§39)")
        report = GeneralizationReport(
            experiment_id=experiment_id,
            in_domain=in_domain,
            unseen=unseen,
            transfer=transfer,
            robustness=robustness,
            gate_passed=not reasons,
            reasons=tuple(reasons),
            created_ts=self._clock.now().isoformat(),
        )
        await self._save(report)
        _log.info(
            "generalization.assessed",
            event_type="adaptation",
            experiment_id=experiment_id,
            passed=report.gate_passed,
            in_domain=round(in_domain, 3),
            unseen=round(unseen, 3),
        )
        return report

    async def latest_for(self, experiment_id: str) -> GeneralizationReport | None:
        cur = await self._db.conn.execute(
            "SELECT * FROM generalization_reports WHERE experiment_id=? ORDER BY id DESC LIMIT 1",
            (experiment_id,),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        d = dict(row)
        return GeneralizationReport(
            experiment_id=str(d["experiment_id"]),
            in_domain=float(d["in_domain"]),
            unseen=float(d["unseen"]),
            transfer=d["transfer"],
            robustness=d["robustness"],
            gate_passed=bool(d["gate_passed"]),
            reasons=tuple(json.loads(str(d["reasons_json"]))),
            created_ts=str(d["created_ts"]),
        )

    async def _save(self, report: GeneralizationReport) -> None:
        await self._db.conn.execute(
            """
            INSERT INTO generalization_reports (
                experiment_id, in_domain, unseen, transfer, robustness,
                gate_passed, reasons_json, created_ts
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                report.experiment_id,
                report.in_domain,
                report.unseen,
                report.transfer,
                report.robustness,
                int(report.gate_passed),
                json.dumps(list(report.reasons)),
                report.created_ts,
            ),
        )
        await self._db.conn.commit()


class RecoveryEvaluator:
    """§41: scores recovery on its own — initial failure, recovery success,
    retries, extra cost and quality after recovery."""

    def __init__(self, *, db: Database, clock: Clock | None = None) -> None:
        self._db = db
        self._clock = clock or SystemClock()

    async def evaluate(
        self,
        trajectory: Trajectory,
        failures: tuple[FailureRecord, ...],
        *,
        additional_cost_usd: float = 0.0,
        quality_after_recovery: float | None = None,
    ) -> RecoveryEvaluation:
        initial_failure = bool(failures)
        recovered = initial_failure and trajectory.success
        first_step = failures[0].step if failures else 0
        recovery_steps = max(0, trajectory.steps_taken - first_step) if recovered else 0
        if not initial_failure:
            score = 1.0  # nothing to recover — clean run
        elif recovered:
            quality = quality_after_recovery if quality_after_recovery is not None else 0.8
            score = min(1.0, 0.6 + 0.4 * quality)
        else:
            score = 0.1  # failed and never recovered
        evaluation = RecoveryEvaluation(
            trajectory_id=trajectory.id,
            initial_failure=initial_failure,
            recovered=recovered,
            recovery_steps=recovery_steps,
            additional_cost_usd=additional_cost_usd,
            quality_after_recovery=quality_after_recovery,
            score=score,
            created_ts=self._clock.now().isoformat(),
        )
        await self._db.conn.execute(
            """
            INSERT INTO recovery_evaluations (
                trajectory_id, initial_failure, recovered, recovery_steps,
                additional_cost_usd, quality_after_recovery, score, created_ts
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                evaluation.trajectory_id,
                int(evaluation.initial_failure),
                int(evaluation.recovered),
                evaluation.recovery_steps,
                evaluation.additional_cost_usd,
                evaluation.quality_after_recovery,
                evaluation.score,
                evaluation.created_ts,
            ),
        )
        await self._db.conn.commit()
        return evaluation


class LongHorizonEvaluator:
    """§42: measures long multi-step trajectories. Returns None below the
    minimum step count — short tasks are simply not long-horizon evidence."""

    def __init__(self, *, db: Database, clock: Clock | None = None) -> None:
        self._db = db
        self._clock = clock or SystemClock()

    async def evaluate(
        self, trajectory: Trajectory, traces: tuple[DecisionTrace, ...], failures: tuple[FailureRecord, ...]
    ) -> LongHorizonResult | None:
        steps = trajectory.steps_taken
        if steps < MIN_LONG_HORIZON_STEPS:
            return None
        goal_completion = 1.0 if trajectory.success else 0.0
        error_accumulation = min(1.0, len(failures) / steps)
        replans = sum(1 for t in traces if t.decision_point == DecisionPoint.REPLANNING)
        plan_drift = min(1.0, 0.25 * replans)
        verification = tuple(t for t in traces if t.decision_point == DecisionPoint.VERIFICATION)
        verification_quality = (
            sum(_OUTCOME_SCORE[t.outcome] for t in verification) / len(verification) if verification else None
        )
        recovery = 1.0 if failures and trajectory.success else (0.0 if failures else None)
        score = (
            0.5 * goal_completion
            + 0.2 * (1.0 - error_accumulation)
            + 0.15 * (1.0 - plan_drift)
            + 0.15 * (verification_quality if verification_quality is not None else 0.5)
        )
        result = LongHorizonResult(
            trajectory_id=trajectory.id,
            steps=steps,
            goal_completion=goal_completion,
            error_accumulation=error_accumulation,
            plan_drift=plan_drift,
            verification_quality=verification_quality,
            recovery=recovery,
            score=score,
            created_ts=self._clock.now().isoformat(),
        )
        await self._db.conn.execute(
            """
            INSERT INTO long_horizon_results (
                trajectory_id, steps, goal_completion, error_accumulation,
                plan_drift, verification_quality, recovery, score, created_ts
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                result.trajectory_id,
                result.steps,
                result.goal_completion,
                result.error_accumulation,
                result.plan_drift,
                result.verification_quality,
                result.recovery,
                result.score,
                result.created_ts,
            ),
        )
        await self._db.conn.commit()
        return result


__all__ = [
    "MIN_LONG_HORIZON_STEPS",
    "MIN_UNSEEN_ABSOLUTE",
    "UNSEEN_COLLAPSE_RATIO",
    "GeneralizationGate",
    "LongHorizonEvaluator",
    "RecoveryEvaluator",
]
