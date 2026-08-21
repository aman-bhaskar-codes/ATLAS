"""Tests for the hypothesis/experiment/promotion pipeline and the
AdaptationEngine (Prompt 4 §15-§24)."""

from __future__ import annotations

from datetime import UTC, datetime

from atlas.adaptation import (
    AdaptationEngine,
    AdaptationStore,
    ArmResults,
    Experiment,
    ExperimentEngine,
    ExperimentStatus,
    FailureClass,
    FailureTaxonomy,
    GeneralizationResult,
    HypothesisGenerator,
    HypothesisStatus,
    HypothesisStore,
    PromotionManager,
    PromotionPolicy,
    PromotionState,
    cluster_failures,
)
from atlas.adaptation.clustering import FailureCluster
from atlas.adaptation.statistics import (
    confidence_interval,
    effect_size,
    is_significant,
    strength_note,
)
from atlas.infra.db import Database
from atlas.memory.trajectory import FailureCategory, FailureRecord, Trajectory


def _trajectory(tid: str, **overrides: object) -> Trajectory:
    now = datetime.now(UTC)
    base: dict[str, object] = {
        "id": tid,
        "task_id": f"task_{tid}",
        "correlation_id": "corr",
        "request": "browse settings page",
        "goal": "click the save button on settings",
        "plan_steps": ("locate", "click"),
        "risk_level": "low",
        "plan_confidence": 0.7,
        "success": False,
        "error": "target not found",
        "steps_taken": 2,
        "latency_ms": 50,
        "tokens_used": 10,
        "cost_usd": 0.001,
        "model_calls": 1,
        "tool_calls": 1,
        "created_ts": now,
        "completed_ts": now,
    }
    base.update(overrides)
    return Trajectory(**base)  # type: ignore[arg-type]


def _failure_record(tid: str) -> FailureRecord:
    return FailureRecord(
        id=f"fr_{tid}",
        task_id=f"task_{tid}",
        correlation_id="corr",
        ts=datetime.now(UTC),
        category=FailureCategory.TOOL_ERROR,
        step=2,
        component="browser",
        error_message="target not found",
    )


def _cluster(n: int = 3) -> FailureCluster:
    failures = tuple(FailureTaxonomy.create(f"traj_{i}", FailureClass.TARGET_GROUNDING_FAILURE) for i in range(n))
    clusters = cluster_failures(failures, components={f.failure_id: "browser" for f in failures})
    return clusters[0]


class TestStatistics:
    def test_no_significance_from_small_n(self) -> None:
        """§22: never fake scientific certainty from 5 tasks."""
        baseline = [0.0, 0.5, 1.0, 0.5, 0.0]
        candidate = [1.0, 1.0, 1.0, 1.0, 1.0]
        ci_low, ci_high = confidence_interval(baseline, candidate, paired=True)
        assert not is_significant(baseline, candidate, ci_low=ci_low, ci_high=ci_high)
        assert "descriptive only" in strength_note(4)
        assert "suggestive" in strength_note(6)

    def test_significance_with_enough_data(self) -> None:
        n = 20
        baseline = [0.4 + 0.1 * (i % 3) for i in range(n)]
        candidate = [0.85 + 0.05 * (i % 3) for i in range(n)]
        ci_low, ci_high = confidence_interval(baseline, candidate, paired=True)
        assert ci_low is not None and ci_low > 0
        assert is_significant(baseline, candidate, ci_low=ci_low, ci_high=ci_high)
        assert effect_size(baseline, candidate) is not None
        assert strength_note(n) == "sufficient for statistical comparison"


class TestHypothesisGenerator:
    def test_needs_repeated_evidence(self) -> None:
        hypothesis = HypothesisGenerator().from_failure_cluster(_cluster(n=2))
        assert hypothesis is None

    def test_proposes_from_repeated_pattern(self) -> None:
        hypothesis = HypothesisGenerator().from_failure_cluster(_cluster(n=4))
        assert hypothesis is not None
        assert hypothesis.affected_component == "browser"
        assert len(hypothesis.evidence) == 4

    def test_safety_blocks_never_become_hypotheses(self) -> None:
        failures = tuple(FailureTaxonomy.create(f"t{i}", FailureClass.SAFETY_BLOCK) for i in range(5))
        clusters = cluster_failures(failures)
        assert HypothesisGenerator().from_failure_cluster(clusters[0]) is None

    def test_high_risk_change_needs_more_evidence(self) -> None:
        # MODEL_FAILURE → MODEL_ROUTING (high-risk): needs 5, not 3.
        failures = tuple(FailureTaxonomy.create(f"t{i}", FailureClass.MODEL_FAILURE) for i in range(3))
        clusters = cluster_failures(failures)
        assert HypothesisGenerator().from_failure_cluster(clusters[0]) is None
        failures5 = tuple(FailureTaxonomy.create(f"t{i}", FailureClass.MODEL_FAILURE) for i in range(5))
        clusters5 = cluster_failures(failures5)
        hypothesis = HypothesisGenerator().from_failure_cluster(clusters5[0])
        assert hypothesis is not None and hypothesis.risk == "MEDIUM"


class TestExperimentEngine:
    async def _engine(self, db: Database) -> ExperimentEngine:
        from atlas.adaptation.experiments import ExperimentStore

        return ExperimentEngine(store=ExperimentStore(db=db))

    async def test_run_produces_paired_comparisons(self, memory_db: Database) -> None:
        engine = await self._engine(memory_db)
        hypothesis = HypothesisGenerator().from_failure_cluster(_cluster(n=4))
        assert hypothesis is not None
        experiment = await engine.create(
            hypothesis, dataset_version="ds1", pipeline_version="p1", atlas_version="0.1.0"
        )

        class Runner:
            async def run_arm(self, exp: Experiment, arm: object) -> ArmResults:
                boost = 0.25 if arm.arm == "CANDIDATE" else 0.0  # type: ignore[attr-defined]
                tasks = {
                    f"task_{i}": {
                        "success_rate": min(1.0, 0.5 + boost + (i % 2) * 0.05),
                        "quality_score": 0.6 + boost,
                        "latency_ms": 100.0,
                        "cost_usd": 0.01,
                    }
                    for i in range(12)
                }
                return ArmResults(per_task=tasks, cost_usd=0.1)

        completed = await engine.run(experiment, Runner())
        assert completed.status is ExperimentStatus.COMPLETED
        comparisons = await engine.comparisons_for(experiment.experiment_id)
        by_metric = {c.metric: c for c in comparisons}
        success = by_metric["success_rate"]
        assert success.paired
        assert success.n == 12
        assert success.candidate_mean > success.baseline_mean
        assert success.significant  # n=12 > threshold, clear delta

    async def test_budget_limit_aborts_safely(self, memory_db: Database) -> None:
        """§86: exceeding the cost limit aborts safely and keeps evidence."""
        from atlas.adaptation.domain import ResourceLimits

        engine = await self._engine(memory_db)
        hypothesis = HypothesisGenerator().from_failure_cluster(_cluster(n=4))
        assert hypothesis is not None
        experiment = await engine.create(
            hypothesis, dataset_version="ds1", pipeline_version="p1", atlas_version="0.1.0"
        )
        cheap = Experiment(**{**experiment.model_dump(), "resource_limits": ResourceLimits(max_cost_usd=0.05)})

        class ExpensiveRunner:
            async def run_arm(self, exp: Experiment, arm: object) -> ArmResults:
                return ArmResults(per_task={"t1": {"success_rate": 1.0}}, cost_usd=0.1)

        aborted = await engine.run(cheap, ExpensiveRunner())
        assert aborted.status is ExperimentStatus.BUDGET_LIMITED


class TestPromotion:
    async def _setup(self, db: Database) -> tuple[PromotionManager, HypothesisStore, Experiment]:
        from atlas.adaptation.experiments import ExperimentEngine, ExperimentStore
        from atlas.adaptation.strategy_versions import StrategyVersionStore

        hypothesis_store = HypothesisStore(db=db)
        experiment_store = ExperimentStore(db=db)
        engine = ExperimentEngine(store=experiment_store)
        hypothesis = HypothesisGenerator().from_failure_cluster(_cluster(n=4))
        assert hypothesis is not None
        await hypothesis_store.save(hypothesis)
        experiment = await engine.create(
            hypothesis, dataset_version="ds1", pipeline_version="p1", atlas_version="0.1.0"
        )
        manager = PromotionManager(
            db=db,
            hypothesis_store=hypothesis_store,
            version_store=StrategyVersionStore(db=db),
            policy=PromotionPolicy(min_evidence=2),
        )
        return manager, hypothesis_store, experiment

    def _comparisons(self, experiment: Experiment, *, delta: float = 0.2):
        from atlas.adaptation.domain import ComparisonResult

        def make(metric: str, base: float, cand: float) -> ComparisonResult:
            return ComparisonResult(
                experiment_id=experiment.experiment_id,
                metric=metric,
                baseline_version="v1",
                candidate_version="v2",
                dataset_version="ds1",
                atlas_version="0.1.0",
                n=12,
                baseline_mean=base,
                candidate_mean=cand,
            )

        return (
            make("success_rate", 0.6, 0.6 + delta),
            make("cost_usd", 0.01, 0.0105),
            make("latency_ms", 100.0, 102.0),
        )

    async def test_safety_regression_always_rejects(self, memory_db: Database) -> None:
        """§23 HARD RULE: even a perfect improvement is rejected."""
        manager, hypothesis_store, experiment = await self._setup(memory_db)
        decision = await manager.decide(experiment, self._comparisons(experiment, delta=0.4), safety_regression=True)
        assert decision.decision == "REJECT"
        assert decision.safety_regression
        assert decision.promotion_state is PromotionState.REJECTED
        hypothesis = await hypothesis_store.get(experiment.hypothesis_id)
        assert hypothesis is not None and hypothesis.status is HypothesisStatus.REJECTED

    async def test_promote_and_rollback(self, memory_db: Database) -> None:
        manager, hypothesis_store, experiment = await self._setup(memory_db)
        generalization = GeneralizationResult(
            experiment_id=experiment.experiment_id,
            baseline_score=0.6,
            candidate_score=0.8,
            n_tasks=5,
            holds_on_unseen=True,
        )
        decision = await manager.decide(experiment, self._comparisons(experiment), generalization=generalization)
        assert decision.decision == "PROMOTE"
        applied = await manager.apply(
            decision,
            strategy_id="s_browser",
            definition="re-locate target before retry",
            change_reason=experiment.experiment_id,
        )
        assert applied.promotion_state is PromotionState.PROMOTED
        assert applied.promoted_version == 1

        rollback = await manager.rollback("s_browser", reason="live regression")
        assert rollback is not None
        assert rollback.promotion_state is PromotionState.ROLLED_BACK
        hypothesis = await hypothesis_store.get(experiment.hypothesis_id)
        assert hypothesis is not None and hypothesis.status is HypothesisStatus.ROLLED_BACK

    async def test_cost_explosion_rejects(self, memory_db: Database) -> None:
        """§14: succeeding more but costing 10x is not superior."""
        manager, _hs, experiment = await self._setup(memory_db)
        from atlas.adaptation.domain import ComparisonResult

        comparisons = (
            ComparisonResult(
                experiment_id=experiment.experiment_id,
                metric="success_rate",
                baseline_version="v1",
                candidate_version="v2",
                dataset_version="ds1",
                atlas_version="0.1.0",
                n=12,
                baseline_mean=0.6,
                candidate_mean=0.62,
            ),
            ComparisonResult(
                experiment_id=experiment.experiment_id,
                metric="cost_usd",
                baseline_version="v1",
                candidate_version="v2",
                dataset_version="ds1",
                atlas_version="0.1.0",
                n=12,
                baseline_mean=0.01,
                candidate_mean=0.1,
            ),
        )
        decision = await manager.decide(experiment, comparisons)
        assert decision.decision == "REJECT"
        assert any("cost increase" in r for r in decision.reasons)

    async def test_missing_generalization_rejects(self, memory_db: Database) -> None:
        manager, _hs, experiment = await self._setup(memory_db)
        decision = await manager.decide(experiment, self._comparisons(experiment))
        assert decision.decision == "REJECT"
        assert any("generalization" in r for r in decision.reasons)


class TestAdaptationEngine:
    async def _engine(self, db: Database) -> AdaptationEngine:
        from atlas.adaptation.experiments import ExperimentEngine, ExperimentStore
        from atlas.adaptation.strategy_versions import StrategyVersionStore

        adaptation_store = AdaptationStore(db=db)
        hypothesis_store = HypothesisStore(db=db)
        experiment_engine = ExperimentEngine(store=ExperimentStore(db=db))
        promotion = PromotionManager(
            db=db,
            hypothesis_store=hypothesis_store,
            version_store=StrategyVersionStore(db=db),
            policy=PromotionPolicy(min_evidence=2),
        )
        return AdaptationEngine(
            adaptation_store=adaptation_store,
            hypothesis_store=hypothesis_store,
            experiment_engine=experiment_engine,
            promotion=promotion,
        )

    async def test_single_failure_creates_no_hypothesis(self, memory_db: Database) -> None:
        """§48: no fake activity when evidence is thin."""
        engine = await self._engine(memory_db)
        report = await engine.run_cycle(((_trajectory("t1"), (), (_failure_record("t1"),)),))
        assert report.hypotheses_proposed == ()
        assert any("idle" in note for note in report.notes)

    async def test_repeated_failure_flows_to_hypothesis(self, memory_db: Database) -> None:
        engine = await self._engine(memory_db)
        items = tuple((_trajectory(f"t{i}"), (), (_failure_record(f"t{i}"),)) for i in range(4))
        report = await engine.run_cycle(items)
        assert len(report.hypotheses_proposed) == 1
        # no runner → hypothesis queued, no experiments faked
        assert report.experiments_run == ()

    async def test_full_cycle_with_runner_reaches_decision(self, memory_db: Database) -> None:
        engine = await self._engine(memory_db)
        items = tuple((_trajectory(f"t{i}"), (), (_failure_record(f"t{i}"),)) for i in range(4))

        class Runner:
            async def run_arm(self, exp: Experiment, arm: object) -> ArmResults:
                boost = 0.3 if arm.arm == "CANDIDATE" else 0.0  # type: ignore[attr-defined]
                tasks = {
                    f"task_{i}": {
                        "success_rate": min(1.0, 0.5 + boost + (i % 2) * 0.04),
                        "quality_score": 0.7,
                        "latency_ms": 90.0,
                        "cost_usd": 0.01,
                    }
                    for i in range(12)
                }
                return ArmResults(per_task=tasks, cost_usd=0.05)

        report = await engine.run_cycle(items, runner=Runner())
        assert len(report.experiments_run) == 1
        assert len(report.decisions) == 1
        # improvement real but generalization missing → REJECT, not fake promote
        assert report.decisions[0].decision == "REJECT"

    async def test_duplicate_component_deduplicates(self, memory_db: Database) -> None:
        engine = await self._engine(memory_db)
        items = tuple((_trajectory(f"t{i}"), (), (_failure_record(f"t{i}"),)) for i in range(4))
        first = await engine.run_cycle(items)
        assert len(first.hypotheses_proposed) == 1
        more = tuple((_trajectory(f"u{i}"), (), (_failure_record(f"u{i}"),)) for i in range(4))
        second = await engine.run_cycle(more)
        assert second.hypotheses_proposed == ()  # same component already has an open hypothesis
