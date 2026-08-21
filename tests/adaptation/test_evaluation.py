"""Tests for the evaluation hierarchy, failure analyzer, clustering and store
(Prompt 4 §4-§8)."""

from __future__ import annotations

from datetime import UTC, datetime

from atlas.adaptation import (
    AdaptationStore,
    EvaluationHierarchy,
    FailureAnalyzer,
    FailureClass,
    FailureTaxonomy,
    RagasInputs,
    candidate_clusters,
    cluster_failures,
)
from atlas.adaptation.domain import EvaluationVerdict
from atlas.infra.db import Database
from atlas.memory.trajectory import (
    DecisionOutcome,
    DecisionPoint,
    DecisionTrace,
    FailureCategory,
    FailureRecord,
    Trajectory,
)


def _trajectory(**overrides: object) -> Trajectory:
    now = datetime.now(UTC)
    base: dict[str, object] = {
        "id": "traj_e1",
        "task_id": "task_1",
        "correlation_id": "corr_1",
        "request": "research steam engines",
        "goal": "research steam engines",
        "plan_steps": ("search", "compare", "verify"),
        "risk_level": "low",
        "plan_confidence": 0.9,
        "success": True,
        "steps_taken": 3,
        "latency_ms": 100,
        "tokens_used": 500,
        "cost_usd": 0.01,
        "model_calls": 2,
        "tool_calls": 1,
        "created_ts": now,
        "completed_ts": now,
    }
    base.update(overrides)
    return Trajectory(**base)  # type: ignore[arg-type]


def _trace(point: DecisionPoint, outcome: DecisionOutcome, *, ts_offset: int = 0) -> DecisionTrace:
    return DecisionTrace(
        id=f"dt_{point.value}_{ts_offset}",
        task_id="task_1",
        correlation_id="corr_1",
        ts=datetime(2026, 1, 1, tzinfo=UTC).replace(second=ts_offset),
        decision_point=point,
        options_considered=("a", "b"),
        chosen_option="a",
        rationale="r",
        outcome=outcome,
    )


def _failure(category: FailureCategory, *, step: int = 1, component: str = "tool_dispatcher") -> FailureRecord:
    return FailureRecord(
        id=f"fr_{category.value}",
        task_id="task_1",
        correlation_id="corr_1",
        ts=datetime(2026, 1, 1, tzinfo=UTC),
        category=category,
        step=step,
        component=component,
        error_message="boom",
    )


class TestEvaluationHierarchy:
    async def test_deterministic_levels_win(self) -> None:
        """§5: deterministic answers are never overridden by higher levels."""
        hierarchy = EvaluationHierarchy()
        outcome = await hierarchy.evaluate(_trajectory(verification_passed=True))
        assert outcome.verdict is EvaluationVerdict.PASS
        scores = outcome.dimensions.scores()
        assert scores["goal_achievement"] == 1.0
        assert scores["verification"] == 1.0
        assert 1 in outcome.dimensions.evaluator_levels
        assert 2 in outcome.dimensions.evaluator_levels

    async def test_failed_trajectory_gets_fail_verdict(self) -> None:
        hierarchy = EvaluationHierarchy()
        outcome = await hierarchy.evaluate(_trajectory(success=False, error="tool died"))
        assert outcome.verdict is EvaluationVerdict.FAIL
        assert outcome.dimensions.scores()["correctness"] == 0.0

    async def test_judge_only_for_unclaimed_dimensions(self) -> None:
        """§5: LLM judge is consulted ONLY for dimensions no lower level
        answered — never for goal_achievement (deterministic)."""
        calls: list[str] = []

        class FakeJudge:
            async def judge_score(self, *, prompt: str, generator_model: str | None) -> float | None:
                calls.append(prompt)
                return 0.7

        hierarchy = EvaluationHierarchy(judge=FakeJudge())
        outcome = await hierarchy.evaluate(_trajectory(), judge_dimensions=("goal_achievement", "planning_quality"))
        assert len(calls) == 1  # only planning_quality was unclaimed
        assert "planning_quality" in calls[0]
        assert outcome.dimensions.scores()["goal_achievement"] == 1.0

    async def test_ragas_level_scores_knowledge_dimensions(self) -> None:
        hierarchy = EvaluationHierarchy()
        outcome = await hierarchy.evaluate(
            _trajectory(),
            ragas_inputs=RagasInputs(
                answer="newcomen engines were atmospheric",
                query="how did newcomen engines work",
                contexts=("the newcomen engine used atmospheric pressure",),
            ),
        )
        assert 4 in outcome.dimensions.evaluator_levels
        assert outcome.dimensions.scores()["knowledge_grounding"] > 0.0

    async def test_human_feedback_is_level_six(self) -> None:
        hierarchy = EvaluationHierarchy()
        outcome = await hierarchy.evaluate(_trajectory(), human_feedback=0.9)
        assert outcome.dimensions.scores()["user_feedback"] == 0.9
        assert 6 in outcome.dimensions.evaluator_levels


class TestFailureAnalyzer:
    def test_symptom_vs_root_cause(self) -> None:
        """§7: last exception (tool error) is the symptom; the failed
        planning decision is the root cause."""
        trajectory = _trajectory(success=False, error="tool failed")
        analysis = FailureAnalyzer().analyze(
            trajectory,
            decision_traces=(
                _trace(DecisionPoint.PLANNING, DecisionOutcome.FAILURE, ts_offset=1),
                _trace(DecisionPoint.TOOL_SELECTION, DecisionOutcome.SUBOPTIMAL, ts_offset=2),
            ),
            failure_records=(_failure(FailureCategory.TOOL_ERROR),),
        )
        assert analysis.primary_cause is FailureClass.PLANNING_FAILURE
        assert FailureClass.TOOL_SELECTION_FAILURE in analysis.secondary_causes
        assert analysis.avoidable
        assert analysis.recommended_intervention

    def test_no_decisions_falls_back_to_symptom(self) -> None:
        trajectory = _trajectory(success=False, error="provider 500")
        analysis = FailureAnalyzer().analyze(trajectory, failure_records=(_failure(FailureCategory.MODEL_ERROR),))
        assert analysis.primary_cause is FailureClass.MODEL_FAILURE
        assert not analysis.avoidable

    def test_repeated_replans_implicate_planning(self) -> None:
        trajectory = _trajectory(success=False, replan_count=3)
        analysis = FailureAnalyzer().analyze(trajectory)
        assert analysis.primary_cause is FailureClass.PLANNING_FAILURE

    def test_safety_blocks_are_never_adaptation_targets(self) -> None:
        trajectory = _trajectory(success=False)
        analysis = FailureAnalyzer().analyze(
            trajectory,
            decision_traces=(_trace(DecisionPoint.SAFETY_TIER, DecisionOutcome.FAILURE),),
            failure_records=(_failure(FailureCategory.SAFETY_BLOCK),),
        )
        assert analysis.primary_cause is FailureClass.SAFETY_BLOCK
        assert not analysis.avoidable


class TestClustering:
    def test_repeated_pattern_becomes_candidate(self) -> None:
        failures = tuple(FailureTaxonomy.create(f"traj_{i}", FailureClass.TARGET_GROUNDING_FAILURE) for i in range(3))
        clusters = cluster_failures(failures)
        assert len(clusters) == 1
        assert clusters[0].count == 3
        assert clusters[0].is_candidate_evidence

    def test_single_event_is_not_evidence(self) -> None:
        failures = (FailureTaxonomy.create("traj_1", FailureClass.MODEL_FAILURE),)
        clusters = cluster_failures(failures)
        assert clusters[0].count == 1
        assert candidate_clusters(clusters) == ()

    def test_component_splits_clusters(self) -> None:
        f1 = FailureTaxonomy.create("traj_1", FailureClass.TOOL_EXECUTION_FAILURE)
        f2 = FailureTaxonomy.create("traj_2", FailureClass.TOOL_EXECUTION_FAILURE)
        clusters = cluster_failures((f1, f2), components={f1.failure_id: "browser", f2.failure_id: "shell"})
        assert len(clusters) == 2


class TestAdaptationStore:
    async def test_failure_and_analysis_round_trip(self, memory_db: Database) -> None:
        store = AdaptationStore(db=memory_db)
        failure = FailureTaxonomy.create("traj_e1", FailureClass.RETRIEVAL_FAILURE)
        await store.save_failure(failure)
        loaded = await store.failures_for_trajectory("traj_e1")
        assert len(loaded) == 1 and loaded[0].failure_class is FailureClass.RETRIEVAL_FAILURE

        analyzer = FailureAnalyzer()
        analysis = analyzer.analyze(_trajectory(success=False), failure_records=(_failure(FailureCategory.TOOL_ERROR),))
        await store.save_analysis(analysis)
        fetched = await store.get_analysis("traj_e1")
        assert fetched is not None
        assert fetched.primary_cause is analysis.primary_cause

    async def test_evaluation_round_trip(self, memory_db: Database) -> None:
        store = AdaptationStore(db=memory_db)
        outcome = await EvaluationHierarchy().evaluate(_trajectory())
        await store.save_evaluation(outcome)
        loaded = await store.get_evaluation("traj_e1")
        assert loaded is not None
        assert loaded.verdict is EvaluationVerdict.PASS
        assert loaded.dimensions.scores()["goal_achievement"] == 1.0

    async def test_events_and_negative_experiences(self, memory_db: Database) -> None:
        store = AdaptationStore(db=memory_db)
        await store.record_event("hypothesis_proposed", ref_id="hyp_1", detail={"risk": "LOW"})
        events = await store.recent_events()
        assert events[0][1] == "hypothesis_proposed"
        await store.save_negative_experience("traj_e1", "never retry deleted files", why_rejected="dangerous")
        negatives = await store.negative_experiences()
        assert negatives[0][1] == "never retry deleted files"

    async def test_recent_failures_filter(self, memory_db: Database) -> None:
        store = AdaptationStore(db=memory_db)
        await store.save_failure(FailureTaxonomy.create("t1", FailureClass.MODEL_FAILURE))
        await store.save_failure(FailureTaxonomy.create("t2", FailureClass.TIMEOUT))
        models = await store.recent_failures(FailureClass.MODEL_FAILURE)
        assert len(models) == 1 and models[0].trajectory_id == "t1"
