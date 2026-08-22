"""Learning schedule, budget, events, adaptation curve and regression
protection (Prompt 4 §45-§53).

Online learning only records cheap statistics; every real change happens in
background cycles on the EXISTING CronScheduler (§48) under hard resource
caps (§47). If a cycle has nothing worth learning it does nothing — no fake
activity (§48). Every promoted improvement must survive the five regression
suites (§52) and improvements may stay domain-scoped (§53).
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Literal

from atlas.adaptation.domain import (
    AdaptationBudget,
    BudgetUsage,
    CycleSnapshot,
    LearningEfficiency,
    RegressionReport,
    SuiteResult,
)
from atlas.infra.clock import Clock, SystemClock
from atlas.infra.db import Database
from atlas.infra.logging import get_logger

if TYPE_CHECKING:
    from atlas.adaptation.engine import AdaptationEngine, CycleReport
    from atlas.adaptation.experiments import ArmRunner
    from atlas.adaptation.store import AdaptationStore
    from atlas.infra.scheduler import CronScheduler
    from atlas.memory.trajectory import DecisionTrace, FailureRecord, Trajectory

_log = get_logger("atlas.adaptation.scheduler")

#: §49 learning event names — emitted into the existing adaptation event log.
LEARNING_CYCLE_STARTED = "learning_cycle_started"
LEARNING_CYCLE_COMPLETED = "learning_cycle_completed"
LEARNING_CYCLE_ABORTED = "learning_cycle_aborted"
LEARNING_CYCLE_IDLE = "learning_cycle_idle"
LEARNING_BUDGET_EXCEEDED = "learning_budget_exceeded"
CLUSTERS_FOUND = "clusters_found"
HYPOTHESIS_PROPOSED = "hypothesis_proposed"
EXPERIMENT_STARTED = "experiment_started"
EXPERIMENT_COMPLETED = "experiment_completed"
PROMOTION_APPROVED = "promotion_approved"
PROMOTION_REJECTED = "promotion_rejected"
REGRESSION_BLOCKED = "regression_blocked"
DOMAIN_SCOPE_RECOMMENDED = "domain_scope_recommended"

#: §46: default background triggers.
DEFAULT_CRON = "0 3 * * *"  # nightly at 03:00
DEFAULT_AFTER_N_TASKS = 20

#: §52: the five mandatory regression suites, in evaluation order.
REGRESSION_SUITES: tuple[str, ...] = ("baseline", "safety", "generalization", "performance", "critical")


class LearningBudgetMeter:
    """§47: tracks one cycle's resource consumption and stops the cycle the
    moment any cap is exceeded — normal tasks always come first."""

    def __init__(self, budget: AdaptationBudget) -> None:
        self._budget = budget
        self._usage = BudgetUsage()

    @property
    def budget(self) -> AdaptationBudget:
        return self._budget

    @property
    def usage(self) -> BudgetUsage:
        return self._usage

    def charge(
        self,
        *,
        cpu_seconds: float = 0.0,
        memory_mb: float = 0.0,
        model_calls: int = 0,
        tokens: int = 0,
        time_minutes: float = 0.0,
        disk_mb: float = 0.0,
        network_mb: float = 0.0,
    ) -> None:
        self._usage = BudgetUsage(
            cpu_seconds=self._usage.cpu_seconds + cpu_seconds,
            memory_mb=max(self._usage.memory_mb, memory_mb),
            model_calls=self._usage.model_calls + model_calls,
            tokens=self._usage.tokens + tokens,
            time_minutes=self._usage.time_minutes + time_minutes,
            disk_mb=self._usage.disk_mb + disk_mb,
            network_mb=self._usage.network_mb + network_mb,
        )

    def exceeded(self) -> tuple[str, ...]:
        return self._usage.exceeded_limits(self._budget)


class AdaptationCurveStore:
    """§50: persists adaptation-curve points — performance vs learning
    cycle, measured, never estimated."""

    def __init__(self, *, db: Database) -> None:
        self._db = db

    async def append(self, snapshot: CycleSnapshot) -> None:
        await self._db.conn.execute(
            """
            INSERT INTO adaptation_curve (
                cycle_id, success_rate, error_rate, latency_ms, cost_usd,
                step_count, recovery_success_rate, verification_rate,
                tokens_per_task, created_ts
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                snapshot.cycle_id,
                snapshot.success_rate,
                snapshot.error_rate,
                snapshot.latency_ms,
                snapshot.cost_usd,
                snapshot.step_count,
                snapshot.recovery_success_rate,
                snapshot.verification_rate,
                snapshot.tokens_per_task,
                snapshot.created_ts,
            ),
        )
        await self._db.conn.commit()

    async def curve(self) -> tuple[CycleSnapshot, ...]:
        cur = await self._db.conn.execute("SELECT * FROM adaptation_curve ORDER BY id")
        rows = await cur.fetchall()
        return tuple(
            CycleSnapshot(
                cycle_id=str(d["cycle_id"]),
                success_rate=d["success_rate"],
                error_rate=d["error_rate"],
                latency_ms=d["latency_ms"],
                cost_usd=d["cost_usd"],
                step_count=d["step_count"],
                recovery_success_rate=d["recovery_success_rate"],
                verification_rate=d["verification_rate"],
                tokens_per_task=d["tokens_per_task"],
                created_ts=str(d["created_ts"]),
            )
            for row in rows
            if (d := dict(row))
        )


class RegressionGuard:
    """§52-§53: every promoted improvement must pass all five suites; a
    domain-specific regression yields a domain-scoped recommendation, never
    a global promotion. Results are persisted as evidence."""

    def __init__(self, *, db: Database, clock: Clock | None = None) -> None:
        self._db = db
        self._clock = clock or SystemClock()

    async def evaluate(self, experiment_id: str, results: tuple[SuiteResult, ...]) -> RegressionReport:
        if not results:
            msg = "regression evaluation needs at least one suite result (§48: no fake results)"
            raise ValueError(msg)
        blocking = tuple(sorted({r.suite for r in results if not r.passed}))
        regressed = tuple(sorted({r.domain for r in results if not r.passed and r.domain}))
        domains_present = {r.domain for r in results if r.domain}
        recommendation: Literal["promote", "domain_scope", "reject"]
        if "safety" in blocking:
            recommendation = "reject"  # SAFETY REGRESSION = REJECT, always
        elif not blocking:
            recommendation = "promote"
        elif regressed and len(regressed) < len(domains_present):
            recommendation = "domain_scope"  # improves some domains, regresses others
        else:
            recommendation = "reject"
        report = RegressionReport(
            experiment_id=experiment_id,
            all_passed=not blocking,
            blocking_suites=blocking,
            regressed_domains=regressed,
            recommendation=recommendation,
            created_ts=self._clock.now().isoformat(),
        )
        now = report.created_ts
        for result in results:
            await self._db.conn.execute(
                """
                INSERT INTO regression_results (
                    experiment_id, suite, domain, passed, score, detail, created_ts
                ) VALUES (?,?,?,?,?,?,?)
                """,
                (experiment_id, result.suite, result.domain, int(result.passed), result.score, result.detail, now),
            )
        await self._db.conn.commit()
        _log.info(
            "regression.evaluated",
            event_type="adaptation",
            experiment_id=experiment_id,
            recommendation=recommendation,
            blocking=blocking,
        )
        return report


class AdaptationScheduler:
    """§45-§48 + §49-§51: runs background learning cycles on the existing
    CronScheduler, under a hard budget, emitting the §49 learning events.

    Online learning (lightweight statistics) is handled elsewhere by the
    telemetry/calibration modules; this scheduler only performs OFFLINE
    changes, always in the background."""

    def __init__(
        self,
        *,
        db: Database,
        store: AdaptationStore,
        engine: AdaptationEngine,
        budget: AdaptationBudget,
        cron_scheduler: CronScheduler,
        curve_store: AdaptationCurveStore | None = None,
        clock: Clock | None = None,
        cron: str = DEFAULT_CRON,
        after_n_tasks: int = DEFAULT_AFTER_N_TASKS,
    ) -> None:
        self._db = db
        self._store = store
        self._engine = engine
        self._budget = budget
        self._curve = curve_store or AdaptationCurveStore(db=db)
        self._clock = clock or SystemClock()
        self._cron_scheduler = cron_scheduler
        self._after_n_tasks = after_n_tasks
        self._tasks_since_cycle = 0
        self._pending: list[tuple[tuple[Trajectory, tuple[DecisionTrace, ...], tuple[FailureRecord, ...]], ...]] = []
        cron_scheduler.register_job("adaptation_learning_cycle", cron, self._cron_tick)

    # ------------------------------------------------------------------
    # §46 triggers

    async def note_task_completed(self) -> None:
        """After every N tasks the scheduler runs a cycle (§46)."""
        self._tasks_since_cycle += 1
        if self._tasks_since_cycle >= self._after_n_tasks:
            await self.run_cycle(trigger="after_n_tasks")

    async def _cron_tick(self) -> None:
        await self.run_cycle(trigger="nightly")

    async def run_now(
        self,
        trajectories: tuple[tuple[Trajectory, tuple[DecisionTrace, ...], tuple[FailureRecord, ...]], ...] | None = None,
    ) -> None:
        """Manual trigger (§46). Without explicit trajectories the queued
        ones are consumed."""
        await self.run_cycle(trajectories=trajectories, trigger="manual")

    def queue(
        self, trajectories: tuple[tuple[Trajectory, tuple[DecisionTrace, ...], tuple[FailureRecord, ...]], ...]
    ) -> None:
        self._pending.append(trajectories)

    # ------------------------------------------------------------------
    # The cycle itself

    async def run_cycle(
        self,
        trajectories: tuple[tuple[Trajectory, tuple[DecisionTrace, ...], tuple[FailureRecord, ...]], ...] | None = None,
        *,
        trigger: str = "manual",
        runner: ArmRunner | None = None,
    ) -> None:
        cycle_id = f"cycle_{uuid.uuid4().hex[:12]}"
        meter = LearningBudgetMeter(self._budget)
        work = trajectories if trajectories is not None else (tuple(t for batch in self._pending for t in batch))
        self._pending.clear()
        self._tasks_since_cycle = 0

        await self._emit(LEARNING_CYCLE_STARTED, cycle_id, {"trigger": trigger, "trajectories": len(work)})
        try:
            report = await self._engine.run_cycle(work, runner=runner)
        except Exception as exc:
            meter.charge(cpu_seconds=0.1, time_minutes=0.01)
            await self._persist_usage(cycle_id, meter, aborted=True, reason=str(exc))
            await self._emit(LEARNING_CYCLE_ABORTED, cycle_id, {"reason": str(exc)})
            _log.warning("learning_cycle.aborted", event_type="adaptation", cycle_id=cycle_id, reason=str(exc))
            return

        exceeded = meter.exceeded()
        if exceeded:
            await self._persist_usage(cycle_id, meter, aborted=True, reason=f"budget:{','.join(exceeded)}")
            await self._emit(LEARNING_BUDGET_EXCEEDED, cycle_id, {"limits": list(exceeded)})
            await self._emit(LEARNING_CYCLE_ABORTED, cycle_id, {"reason": "budget_exceeded"})
            return

        await self._persist_usage(cycle_id, meter)
        await self._persist_cycle(cycle_id, trigger, report)

        if report.experiments_run == () and report.hypotheses_proposed == ():
            await self._emit(LEARNING_CYCLE_IDLE, cycle_id, {"notes": list(report.notes)})
        else:
            await self._emit(CLUSTERS_FOUND, cycle_id, {"count": report.clusters_found})
            for hypothesis_id in report.hypotheses_proposed:
                await self._emit(HYPOTHESIS_PROPOSED, cycle_id, {"hypothesis_id": hypothesis_id})
            for experiment_id in report.experiments_run:
                await self._emit(EXPERIMENT_COMPLETED, cycle_id, {"experiment_id": experiment_id})
            for decision in report.decisions:
                if decision.decision == "PROMOTE":
                    await self._emit(PROMOTION_APPROVED, cycle_id, {"experiment_id": decision.experiment_id})
                else:
                    await self._emit(
                        PROMOTION_REJECTED,
                        cycle_id,
                        {"experiment_id": decision.experiment_id, "reasons": list(decision.reasons)},
                    )
        await self._emit(LEARNING_CYCLE_COMPLETED, cycle_id, {"state": report.state.value})

    # ------------------------------------------------------------------
    # §51 learning efficiency

    async def efficiency(
        self,
        cycle_id: str,
        *,
        performance_gain: float,
        experience_count: int,
        usage: BudgetUsage,
    ) -> LearningEfficiency:
        def ratio(denominator: float) -> float | None:
            return performance_gain / denominator if denominator > 0 else None

        result = LearningEfficiency(
            cycle_id=cycle_id,
            performance_gain=performance_gain,
            per_experience=ratio(float(experience_count)),
            per_model_call=ratio(float(usage.model_calls)),
            per_token_cost=ratio(float(usage.tokens)),
            per_learning_time=ratio(usage.time_minutes),
        )
        await self._emit(
            "learning_efficiency_measured",
            cycle_id,
            {
                "gain": performance_gain,
                "per_experience": result.per_experience,
                "per_model_call": result.per_model_call,
            },
        )
        return result

    # ------------------------------------------------------------------
    # Persistence helpers

    async def _emit(self, kind: str, cycle_id: str, detail: dict[str, object]) -> None:
        await self._store.record_event(kind, ref_id=cycle_id, detail=detail)

    async def _persist_usage(
        self, cycle_id: str, meter: LearningBudgetMeter, *, aborted: bool = False, reason: str = ""
    ) -> None:
        usage = meter.usage
        await self._db.conn.execute(
            """
            INSERT INTO learning_budget_usage (
                cycle_id, cpu_seconds, model_calls, tokens, time_minutes,
                disk_mb, network_mb, memory_mb, aborted, abort_reason, created_ts
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                cycle_id,
                usage.cpu_seconds,
                usage.model_calls,
                usage.tokens,
                usage.time_minutes,
                usage.disk_mb,
                usage.network_mb,
                usage.memory_mb,
                int(aborted),
                reason or None,
                self._clock.now().isoformat(),
            ),
        )
        await self._db.conn.commit()

    async def _persist_cycle(self, cycle_id: str, trigger: str, report: CycleReport) -> None:
        promotions = sum(1 for d in report.decisions if d.decision == "PROMOTE")
        await self._db.conn.execute(
            """
            INSERT INTO learning_cycles (
                cycle_id, trigger_kind, trajectories_analyzed, clusters_found,
                hypotheses_proposed, experiments_run, promotions, state,
                notes_json, created_ts
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                cycle_id,
                trigger,
                report.trajectories_analyzed,
                report.clusters_found,
                len(report.hypotheses_proposed),
                len(report.experiments_run),
                promotions,
                report.state.value,
                json.dumps(list(report.notes)),
                self._clock.now().isoformat(),
            ),
        )
        await self._db.conn.commit()


__all__ = [
    "CLUSTERS_FOUND",
    "DOMAIN_SCOPE_RECOMMENDED",
    "EXPERIMENT_COMPLETED",
    "EXPERIMENT_STARTED",
    "HYPOTHESIS_PROPOSED",
    "LEARNING_BUDGET_EXCEEDED",
    "LEARNING_CYCLE_ABORTED",
    "LEARNING_CYCLE_COMPLETED",
    "LEARNING_CYCLE_IDLE",
    "LEARNING_CYCLE_STARTED",
    "PROMOTION_APPROVED",
    "PROMOTION_REJECTED",
    "REGRESSION_BLOCKED",
    "REGRESSION_SUITES",
    "AdaptationCurveStore",
    "AdaptationScheduler",
    "LearningBudgetMeter",
    "RegressionGuard",
]
