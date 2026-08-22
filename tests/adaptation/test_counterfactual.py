"""Tests for shadow mode, canary rollout, the counterfactual engine, replay
safety and decision quality (Prompt 4 §25-§31)."""

from __future__ import annotations

from datetime import UTC, datetime

from atlas.adaptation import (
    AdaptationPoint,
    CanaryManager,
    CanaryObservation,
    CanaryStatus,
    CounterfactualEngine,
    DecisionQualityStore,
    InMemoryReplayEnvironment,
    PreferenceStore,
    ProcessEvaluator,
    ShadowDecision,
    ShadowEvaluator,
    ShadowStore,
    ShadowVerdict,
    mode_for,
    replay_allowed,
)
from atlas.adaptation.counterfactual import DEFAULT_PREFERENCE_EVIDENCE
from atlas.infra.db import Database
from atlas.memory.trajectory import (
    DecisionOutcome,
    DecisionPoint,
    DecisionTrace,
    FailureCategory,
    FailureRecord,
    Trajectory,
)


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


def _trace(
    tid: str,
    point: DecisionPoint,
    options: tuple[str, ...],
    chosen: str,
    outcome: DecisionOutcome,
    *,
    i: int = 0,
) -> DecisionTrace:
    return DecisionTrace(
        id=f"dt_{tid}_{i}",
        task_id=f"task_{tid}",
        correlation_id="corr",
        ts=datetime.now(UTC),
        decision_point=point,
        options_considered=options,
        chosen_option=chosen,
        rationale="test",
        outcome=outcome,
    )


class TestReplaySafety:
    def test_never_replay_classes_refused_by_default(self) -> None:
        """§28: email sends, payments, deletions, external mutations and
        credential operations are never replayed by default."""
        for action_class in ("email_send", "payment", "deletion", "external_mutation", "credential_operation"):
            assert not replay_allowed(action_class)
            assert mode_for(action_class) is None

    def test_never_replay_allowed_with_isolated_simulator(self) -> None:
        assert replay_allowed("email_send", has_isolated_simulator=True)

    def test_replayable_classes_allowed(self) -> None:
        for action_class in ("deterministic", "sandbox", "golden", "recorded", "simulation", "dry_run"):
            assert replay_allowed(action_class)
            assert mode_for(action_class) is not None

    def test_unknown_class_refused(self) -> None:
        """Deny-by-default: anything not on the allow-list is refused."""
        assert not replay_allowed("anything_else")

    def test_in_memory_environment_snapshot_restore_replay(self) -> None:
        env = InMemoryReplayEnvironment(step_fn=lambda state, action: (action == "tool_b", 1.0))
        snap = env.snapshot({"page": "settings"})
        assert env.restore(snap) == {"page": "settings"}
        outcome = env.replay(snap, "tool_b")
        assert outcome.success and outcome.score == 1.0


class TestShadowMode:
    async def test_candidate_better_verdict(self, memory_db: Database) -> None:
        evaluator = ShadowEvaluator(store=ShadowStore(db=memory_db))
        comparison = await evaluator.compare(
            _trajectory("t1"),
            ShadowDecision(decision="click the save button on settings", plan=("locate", "click"), expected_result=0.9),
            strategy_id="s1",
            baseline_version=1,
            candidate_version=2,
            actual_result=0.6,
        )
        assert comparison.verdict is ShadowVerdict.CANDIDATE_BETTER
        assert comparison.plan_similarity == 1.0
        assert comparison.expected_result_delta > 0.25

    async def test_equivalent_inside_band(self, memory_db: Database) -> None:
        evaluator = ShadowEvaluator(store=ShadowStore(db=memory_db))
        comparison = await evaluator.compare(
            _trajectory("t1"),
            ShadowDecision(expected_result=0.62),
            strategy_id="s1",
            baseline_version=1,
            candidate_version=2,
            actual_result=0.6,
        )
        assert comparison.verdict is ShadowVerdict.EQUIVALENT

    async def test_store_roundtrip(self, memory_db: Database) -> None:
        store = ShadowStore(db=memory_db)
        evaluator = ShadowEvaluator(store=store)
        await evaluator.compare(
            _trajectory("t1"),
            ShadowDecision(expected_result=0.3),
            strategy_id="s1",
            baseline_version=1,
            candidate_version=2,
            actual_result=0.6,
        )
        saved = await store.for_strategy("s1")
        assert len(saved) == 1
        assert saved[0].verdict is ShadowVerdict.CANDIDATE_WORSE


class TestCanary:
    async def test_deploys_at_smallest_step(self, memory_db: Database) -> None:
        manager = CanaryManager(db=memory_db, min_tasks=2)
        deployment = await manager.deploy("s1", version=2)
        assert deployment.percentage == 5.0
        assert deployment.status is CanaryStatus.CANARY
        fetched = await manager.get(deployment.deployment_id)
        assert fetched is not None and fetched.strategy_id == "s1"

    def test_routing_is_deterministic(self) -> None:
        from pathlib import Path

        from atlas.adaptation.domain import CanaryDeployment

        manager = CanaryManager(db=Database(Path(":memory:")), min_tasks=2)
        deployment = CanaryDeployment(strategy_id="s1", version=2, percentage=50.0)
        task = "task_abc"
        assert manager.route_task(deployment, task) == manager.route_task(deployment, task)
        # the same percentage routes a stable fraction of tasks to the canary
        routed = sum(1 for i in range(200) if manager.route_task(deployment, f"task_{i}"))
        assert 50 < routed < 150

    async def test_safety_event_rolls_back_immediately(self, memory_db: Database) -> None:
        manager = CanaryManager(db=memory_db, min_tasks=100)
        deployment = await manager.deploy("s1", version=2)
        await manager.observe(
            CanaryObservation(
                deployment_id=deployment.deployment_id, trajectory_id="t1", success=True, safety_event=True
            )
        )
        updated = await manager.evaluate(deployment, baseline_success_rate=0.6)
        assert updated.status is CanaryStatus.ROLLED_BACK

    async def test_severe_regression_rolls_back(self, memory_db: Database) -> None:
        manager = CanaryManager(db=memory_db, min_tasks=2)
        deployment = await manager.deploy("s1", version=2)
        for i in range(2):
            await manager.observe(
                CanaryObservation(deployment_id=deployment.deployment_id, trajectory_id=f"t{i}", success=False)
            )
        updated = await manager.evaluate(deployment, baseline_success_rate=0.9)
        assert updated.status is CanaryStatus.ROLLED_BACK

    async def test_good_metrics_expand_through_steps_to_full(self, memory_db: Database) -> None:
        manager = CanaryManager(db=memory_db, min_tasks=2)
        deployment = await manager.deploy("s1", version=2)
        expected = (10.0, 25.0, 50.0, 100.0)
        current = deployment
        for i, next_pct in enumerate(expected):
            for j in range(2):
                await manager.observe(
                    CanaryObservation(deployment_id=deployment.deployment_id, trajectory_id=f"t{i}_{j}", success=True)
                )
            current = await manager.evaluate(current, baseline_success_rate=0.6)
            assert current.percentage == next_pct
        assert current.status is CanaryStatus.FULL

    async def test_insufficient_evidence_holds(self, memory_db: Database) -> None:
        """Never expand on thin evidence (§48 applied to canaries)."""
        manager = CanaryManager(db=memory_db, min_tasks=5)
        deployment = await manager.deploy("s1", version=2)
        await manager.observe(
            CanaryObservation(deployment_id=deployment.deployment_id, trajectory_id="t1", success=True)
        )
        updated = await manager.evaluate(deployment, baseline_success_rate=0.6)
        assert updated.percentage == 5.0
        assert updated.status is CanaryStatus.CANARY


class TestCounterfactualEngine:
    def _env(self) -> InMemoryReplayEnvironment:
        return InMemoryReplayEnvironment(step_fn=lambda state, action: (action == "tool_b", 1.0))

    async def test_refuses_non_replayable_actions(self, memory_db: Database) -> None:
        """§28: no counterfactuals for real external mutations."""
        engine = CounterfactualEngine(db=memory_db)
        traces = (_trace("t1", DecisionPoint.TOOL_SELECTION, ("tool_a", "tool_b"), "tool_a", DecisionOutcome.FAILURE),)
        results = await engine.generate(_trajectory("t1"), traces, action_class="email_send")
        assert results == ()

    async def test_generates_alternatives_from_considered_options(self, memory_db: Database) -> None:
        engine = CounterfactualEngine(db=memory_db)
        traces = (
            _trace("t1", DecisionPoint.TOOL_SELECTION, ("tool_a", "tool_b"), "tool_a", DecisionOutcome.FAILURE),
            _trace("t1", DecisionPoint.SAFETY_TIER, ("tier1", "tier2"), "tier1", DecisionOutcome.FAILURE, i=1),
            _trace("t1", DecisionPoint.MODEL_SELECTION, ("m1", "m2"), "m1", DecisionOutcome.SUCCESS, i=2),
        )
        results = await engine.generate(
            _trajectory("t1"), traces, action_class="sandbox", replay=self._env(), environment_state={"page": "x"}
        )
        # only the failed tool-selection trace is counterfactualized:
        # SAFETY_TIER is never a target, the successful model choice is kept
        assert len(results) == 1
        cf = results[0]
        assert cf.adaptation_point is AdaptationPoint.TOOL_SELECTION
        assert cf.original_option == "tool_a"
        assert cf.alternative_option == "tool_b"
        assert cf.alternative_outcome == "success"
        assert cf.delta > 0

    async def test_no_simulator_means_unknown_outcome_never_faked(self, memory_db: Database) -> None:
        engine = CounterfactualEngine(db=memory_db)
        traces = (_trace("t1", DecisionPoint.TOOL_SELECTION, ("tool_a", "tool_b"), "tool_a", DecisionOutcome.FAILURE),)
        results = await engine.generate(_trajectory("t1"), traces, action_class="sandbox")
        assert len(results) == 1
        assert results[0].alternative_outcome == ""
        assert results[0].delta == 0.0

    async def test_learn_preferences_from_repeated_wins(self, memory_db: Database) -> None:
        """§30: alternative wins across N comparable trajectories →
        DecisionPreference stored as evidence."""
        engine = CounterfactualEngine(db=memory_db)
        for i in range(DEFAULT_PREFERENCE_EVIDENCE):
            traces = (
                _trace(f"t{i}", DecisionPoint.TOOL_SELECTION, ("tool_a", "tool_b"), "tool_a", DecisionOutcome.FAILURE),
            )
            await engine.generate(
                _trajectory(f"t{i}"), traces, action_class="sandbox", replay=self._env(), environment_state={}
            )
        prefs = await engine.learn_preferences()
        assert len(prefs) == 1
        pref = prefs[0]
        assert pref.adaptation_point is AdaptationPoint.TOOL_SELECTION
        assert pref.preferred_option == "tool_b"
        assert pref.evidence_count == DEFAULT_PREFERENCE_EVIDENCE
        assert pref.success_rate == 1.0
        # stored and retrievable
        stored = await PreferenceStore(db=memory_db).active_for(AdaptationPoint.TOOL_SELECTION)
        assert stored is not None and stored.preferred_option == "tool_b"
        # idempotent: learning again creates nothing new
        assert await engine.learn_preferences() == ()

    async def test_no_preference_from_thin_evidence(self, memory_db: Database) -> None:
        engine = CounterfactualEngine(db=memory_db)
        for i in range(DEFAULT_PREFERENCE_EVIDENCE - 1):
            traces = (
                _trace(f"t{i}", DecisionPoint.TOOL_SELECTION, ("tool_a", "tool_b"), "tool_a", DecisionOutcome.FAILURE),
            )
            await engine.generate(
                _trajectory(f"t{i}"), traces, action_class="sandbox", replay=self._env(), environment_state={}
            )
        assert await engine.learn_preferences() == ()


class TestProcessEvaluator:
    async def test_scores_every_dimension_from_evidence(self, memory_db: Database) -> None:
        trajectory = _trajectory("t1")
        traces = (
            _trace("t1", DecisionPoint.MODEL_SELECTION, ("m1", "m2"), "m1", DecisionOutcome.SUCCESS),
            _trace("t1", DecisionPoint.TOOL_SELECTION, ("tool_a", "tool_b"), "tool_a", DecisionOutcome.FAILURE, i=1),
            _trace("t1", DecisionPoint.VERIFICATION, ("full", "quick"), "quick", DecisionOutcome.SUCCESS, i=2),
        )
        failures = (
            FailureRecord(
                id="fr1",
                task_id="task_t1",
                correlation_id="corr",
                ts=datetime.now(UTC),
                category=FailureCategory.TOOL_ERROR,
                step=2,
                component="browser",
                error_message="target not found",
            ),
        )
        qualities = ProcessEvaluator().evaluate(trajectory, traces, failures)
        by_dim = {q.dimension: q for q in qualities}
        assert set(by_dim) == {"model_selection", "tool_selection", "strategy", "retrieval", "verification", "recovery"}
        assert by_dim["model_selection"].score == 1.0
        assert by_dim["tool_selection"].score == 0.0
        assert by_dim["tool_selection"].better_alternative == "tool_b"
        assert by_dim["strategy"].score == 0.0  # failed task
        assert by_dim["retrieval"].score == 1.0  # browser failure is not retrieval
        assert by_dim["recovery"].score == 0.2  # failures but no recovery

        store = DecisionQualityStore(db=memory_db)
        await store.save_many(qualities)
        saved = await store.for_trajectory("t1")
        assert len(saved) == len(qualities)

    async def test_recovery_scored_high_when_recovered(self, memory_db: Database) -> None:
        trajectory = _trajectory("t1", success=True, error=None)
        failures = (
            FailureRecord(
                id="fr1",
                task_id="task_t1",
                correlation_id="corr",
                ts=datetime.now(UTC),
                category=FailureCategory.TOOL_ERROR,
                step=1,
                component="browser",
                error_message="transient, retried",
            ),
        )
        qualities = ProcessEvaluator().evaluate(trajectory, (), failures)
        by_dim = {q.dimension: q for q in qualities}
        assert by_dim["recovery"].score == 1.0
        assert by_dim["strategy"].score == 1.0
