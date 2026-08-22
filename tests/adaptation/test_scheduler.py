"""Tests for the learning schedule, budget, adaptation curve, learning
efficiency and regression protection (Prompt 4 §45-§53)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from atlas.adaptation import (
    AdaptationBudget,
    AdaptationCurveStore,
    AdaptationEngine,
    AdaptationScheduler,
    AdaptationStore,
    BudgetUsage,
    CycleSnapshot,
    ExperimentEngine,
    ExperimentStore,
    HypothesisStore,
    LearningBudgetMeter,
    PromotionManager,
    PromotionPolicy,
    RegressionGuard,
    StrategyVersionStore,
    SuiteResult,
)
from atlas.infra.clock import SystemClock
from atlas.infra.db import Database
from atlas.infra.ids import UuidGenerator
from atlas.infra.scheduler import CronScheduler
from atlas.memory.trajectory import FailureCategory, FailureRecord, Trajectory


def _trajectory(tid: str) -> Trajectory:
    now = datetime.now(UTC)
    return Trajectory(
        id=tid,
        task_id=f"task_{tid}",
        correlation_id="corr",
        request="research task",
        goal="summarize the topic",
        plan_steps=("search", "read"),
        risk_level="low",
        plan_confidence=0.7,
        success=False,
        steps_taken=3,
        latency_ms=50,
        tokens_used=10,
        cost_usd=0.001,
        model_calls=1,
        tool_calls=1,
        created_ts=now,
        completed_ts=now,
    )


def _failure(tid: str) -> FailureRecord:
    return FailureRecord(
        id=f"fr_{tid}",
        task_id=f"task_{tid}",
        correlation_id="corr",
        ts=datetime.now(UTC),
        category=FailureCategory.TOOL_ERROR,
        step=1,
        component="browser",
        error_message="element not found",
    )


async def _engine(db: Database) -> AdaptationEngine:
    hypothesis_store = HypothesisStore(db=db)
    promotion = PromotionManager(
        db=db,
        hypothesis_store=hypothesis_store,
        version_store=StrategyVersionStore(db=db),
        policy=PromotionPolicy(min_evidence=2),
    )
    return AdaptationEngine(
        adaptation_store=AdaptationStore(db=db),
        hypothesis_store=hypothesis_store,
        experiment_engine=ExperimentEngine(store=ExperimentStore(db=db)),
        promotion=promotion,
    )


async def _scheduler(db: Database, *, after_n_tasks: int = 20) -> tuple[AdaptationScheduler, AdaptationStore]:
    store = AdaptationStore(db=db)
    cron = CronScheduler(db=db, ids=UuidGenerator(), clock=SystemClock())
    scheduler = AdaptationScheduler(
        db=db,
        store=store,
        engine=await _engine(db),
        budget=AdaptationBudget(),
        cron_scheduler=cron,
        after_n_tasks=after_n_tasks,
    )
    return scheduler, store


class TestLearningBudgetMeter:
    def test_charge_accumulates_and_detects_exceeded(self) -> None:
        meter = LearningBudgetMeter(AdaptationBudget(model_calls=2, tokens=100))
        meter.charge(model_calls=2, tokens=80)
        assert meter.exceeded() == ()
        meter.charge(model_calls=1, tokens=50)
        assert set(meter.exceeded()) == {"model_calls", "tokens"}

    def test_memory_mb_is_peak_not_sum(self) -> None:
        meter = LearningBudgetMeter(AdaptationBudget(memory_mb=100))
        meter.charge(memory_mb=90)
        meter.charge(memory_mb=90)
        assert meter.usage.memory_mb == 90  # peak tracking, not 180


class TestAdaptationScheduler:
    async def test_idle_cycle_emits_idle_event_no_fake_activity(self, memory_db: Database) -> None:
        """§48: without evidence the cycle does nothing — but is logged."""
        scheduler, store = await _scheduler(memory_db)
        await scheduler.run_cycle(((_trajectory("t1"), (), (_failure("t1"),)),))
        kinds = {kind for _ts, kind, _ref, _detail in await store.recent_events()}
        assert "learning_cycle_started" in kinds
        assert "learning_cycle_idle" in kinds
        assert "learning_cycle_completed" in kinds
        assert "hypothesis_proposed" not in kinds  # no fabricated learning

    async def test_repeated_failures_emit_hypothesis_events(self, memory_db: Database) -> None:
        """§49: real learning activity emits the named events."""
        scheduler, store = await _scheduler(memory_db)
        items = tuple((_trajectory(f"t{i}"), (), (_failure(f"t{i}"),)) for i in range(4))
        await scheduler.run_cycle(items)
        kinds = [kind for _ts, kind, _ref, _detail in await store.recent_events()]
        assert "clusters_found" in kinds
        assert "hypothesis_proposed" in kinds

    async def test_broken_engine_aborts_cycle_without_raising(self, memory_db: Database) -> None:
        scheduler, store = await _scheduler(memory_db)

        class Broken:
            async def run_cycle(self, *args: object, **kwargs: object) -> None:
                msg = "boom"
                raise RuntimeError(msg)

        scheduler._engine = Broken()  # type: ignore[assignment]
        await scheduler.run_cycle(((_trajectory("t1"), (), ()),))  # must not raise
        kinds = [kind for _ts, kind, _ref, _detail in await store.recent_events()]
        assert "learning_cycle_aborted" in kinds

    async def test_after_n_tasks_trigger(self, memory_db: Database) -> None:
        """§46: a cycle runs automatically after N completed tasks."""
        scheduler, store = await _scheduler(memory_db, after_n_tasks=2)
        await scheduler.note_task_completed()
        assert not any(kind == "learning_cycle_started" for _ts, kind, _ref, _d in await store.recent_events())
        await scheduler.note_task_completed()
        kinds = [kind for _ts, kind, _ref, _detail in await store.recent_events()]
        assert kinds.count("learning_cycle_started") == 1

    async def test_queued_trajectories_consumed_by_run_now(self, memory_db: Database) -> None:
        """§46 manual trigger consumes queued trajectories."""
        scheduler, _store = await _scheduler(memory_db)
        scheduler.queue(((_trajectory("t1"), (), (_failure("t1"),)),))
        await scheduler.run_now()
        cur = await memory_db.conn.execute("SELECT trajectories_analyzed FROM learning_cycles")
        rows = await cur.fetchall()
        assert len(rows) == 1 and int(rows[0]["trajectories_analyzed"]) == 1

    async def test_efficiency_reports_none_for_zero_denominators(self, memory_db: Database) -> None:
        """§51: undefined ratios are None — never fabricated numbers."""
        scheduler, _store = await _scheduler(memory_db)
        result = await scheduler.efficiency("cycle_x", performance_gain=0.1, experience_count=0, usage=BudgetUsage())
        assert result.performance_gain == 0.1
        assert result.per_experience is None
        assert result.per_model_call is None
        result = await scheduler.efficiency(
            "cycle_y", performance_gain=0.1, experience_count=5, usage=BudgetUsage(model_calls=10, time_minutes=2.0)
        )
        assert result.per_experience == pytest.approx(0.02)
        assert result.per_model_call == pytest.approx(0.01)
        assert result.per_learning_time == pytest.approx(0.05)


class TestAdaptationCurve:
    async def test_curve_points_roundtrip(self, memory_db: Database) -> None:
        """§50: performance vs learning cycle is persisted."""
        curve_store = AdaptationCurveStore(db=memory_db)
        await curve_store.append(CycleSnapshot(cycle_id="c1", success_rate=0.5, latency_ms=1200.0))
        await curve_store.append(CycleSnapshot(cycle_id="c2", success_rate=0.8, latency_ms=900.0))
        curve = await curve_store.curve()
        assert len(curve) == 2
        assert curve[0].success_rate == 0.5 and curve[1].success_rate == 0.8


class TestRegressionGuard:
    async def test_all_suites_pass_promotes(self, memory_db: Database) -> None:
        guard = RegressionGuard(db=memory_db)
        suites = ("baseline", "safety", "generalization", "performance", "critical")
        results = tuple(SuiteResult(suite=s, passed=True) for s in suites)  # type: ignore[arg-type]
        report = await guard.evaluate("exp1", results)
        assert report.all_passed and report.recommendation == "promote"

    async def test_safety_failure_always_rejects(self, memory_db: Database) -> None:
        """§52: SAFETY REGRESSION = REJECT, always."""
        guard = RegressionGuard(db=memory_db)
        results = (
            SuiteResult(suite="baseline", passed=True),
            SuiteResult(suite="safety", passed=False, detail="permission escalation detected"),
        )
        report = await guard.evaluate("exp1", results)
        assert report.recommendation == "reject"
        assert "safety" in report.blocking_suites

    async def test_domain_regression_recommends_domain_scope(self, memory_db: Database) -> None:
        """§52-§53: improves research but breaks coding → domain-scoped,
        never a global promotion."""
        guard = RegressionGuard(db=memory_db)
        results = (
            SuiteResult(suite="performance", domain="research", passed=True),
            SuiteResult(suite="performance", domain="coding", passed=False),
            SuiteResult(suite="baseline", passed=True),
        )
        report = await guard.evaluate("exp1", results)
        assert report.recommendation == "domain_scope"
        assert report.regressed_domains == ("coding",)

    async def test_empty_results_rejected(self, memory_db: Database) -> None:
        guard = RegressionGuard(db=memory_db)
        with pytest.raises(ValueError, match="at least one suite"):
            await guard.evaluate("exp1", ())
