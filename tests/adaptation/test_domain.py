"""Domain-model tests for the adaptation plane (Prompt 4 §1-§3, §6, §13-§24)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from atlas.adaptation import (
    AdaptationPoint,
    AllowedChangeType,
    ComparisonResult,
    DecisionPreference,
    Experiment,
    ExperimentArm,
    FailureAnalysis,
    ForbiddenChangeType,
    Hypothesis,
    HypothesisStatus,
    OutcomeEvaluation,
    PromotionDecision,
    StrategyVersion,
    TrajectoryEvaluation,
)
from atlas.adaptation.taxonomy import FailureClass, FailureDomain, FailureTaxonomy, domain_of
from atlas.infra.db import Database
from atlas.memory.trajectory import Trajectory


def _trajectory(**overrides: object) -> Trajectory:
    now = datetime.now(UTC)
    base: dict[str, object] = {
        "id": "traj_1",
        "task_id": "task_1",
        "correlation_id": "corr_1",
        "request": "do the thing",
        "goal": "do the thing",
        "plan_steps": ("step",),
        "risk_level": "low",
        "plan_confidence": 0.9,
        "success": True,
        "steps_taken": 1,
        "latency_ms": 10,
        "tokens_used": 100,
        "cost_usd": 0.001,
        "model_calls": 1,
        "tool_calls": 0,
        "created_ts": now,
        "completed_ts": now,
    }
    base.update(overrides)
    return Trajectory(**base)  # type: ignore[arg-type]


class TestTaxonomy:
    def test_has_exactly_23_classes(self) -> None:
        assert len(FailureClass) == 23

    def test_every_class_maps_to_a_domain(self) -> None:
        for failure_class in FailureClass:
            assert isinstance(domain_of(failure_class), FailureDomain)

    def test_taxonomy_record_defaults(self) -> None:
        record = FailureTaxonomy.create("traj_1", FailureClass.TOOL_EXECUTION_FAILURE)
        assert record.failure_id.startswith("flt_")
        assert record.root_cause_candidate is False
        assert record.final_resolution == "FAILED"


class TestEvaluation:
    def test_scores_returns_only_scored_dimensions(self) -> None:
        evaluation = TrajectoryEvaluation(trajectory_id="t1", correctness=0.8, safety=1.0)
        assert evaluation.scores() == {"correctness": 0.8, "safety": 1.0}

    def test_outcome_evaluation_wraps_dimensions(self) -> None:
        from atlas.adaptation import EvaluationVerdict

        dimensions = TrajectoryEvaluation(trajectory_id="t1", goal_achievement=0.9)
        outcome = OutcomeEvaluation(
            trajectory_id="t1",
            verdict=EvaluationVerdict.PASS,
            overall_score=0.9,
            dimensions=dimensions,
        )
        assert outcome.verdict is EvaluationVerdict.PASS


class TestFailureAnalysis:
    def test_root_cause_model(self) -> None:
        analysis = FailureAnalysis(
            trajectory_id="t1",
            primary_cause=FailureClass.PLANNING_FAILURE,
            secondary_causes=(FailureClass.TOOL_EXECUTION_FAILURE,),
            confidence=0.7,
            avoidable=True,
            recommended_intervention="choose filesystem tool before browser",
        )
        assert analysis.primary_cause is FailureClass.PLANNING_FAILURE


class TestHypothesis:
    def test_defaults_to_proposed(self) -> None:
        hypothesis = Hypothesis(
            title="t",
            problem_statement="p",
            affected_component="routing",
            proposed_change="c",
            change_type=AllowedChangeType.MODEL_ROUTING,
        )
        assert hypothesis.status is HypothesisStatus.PROPOSED
        assert hypothesis.hypothesis_id.startswith("hyp_")

    def test_change_types_cover_allowed_and_forbidden(self) -> None:
        assert len(AllowedChangeType) == 11
        assert len(ForbiddenChangeType) == 5
        assert ForbiddenChangeType.SAFETY_ENGINE.value == "SAFETY_ENGINE"


class TestExperiment:
    def test_arms_and_limits(self) -> None:
        experiment = Experiment(
            hypothesis_id="hyp_1",
            baseline=ExperimentArm(arm="BASELINE"),
            candidate=ExperimentArm(arm="CANDIDATE", strategy_version="s1:2"),
            dataset_version="ds_1",
            pipeline_version="p_1",
            atlas_version="0.1.0",
        )
        assert experiment.resource_limits.max_tasks == 50
        assert experiment.baseline.arm == "BASELINE"

    def test_comparison_result_stores_baseline_discipline(self) -> None:
        comparison = ComparisonResult(
            experiment_id="exp_1",
            metric="success_rate",
            baseline_version="s1:1",
            candidate_version="s1:2",
            dataset_version="ds_1",
            atlas_version="0.1.0",
            n=20,
            baseline_mean=0.7,
            candidate_mean=0.85,
            paired=True,
        )
        assert comparison.candidate_mean > comparison.baseline_mean


class TestStrategyVersion:
    def test_immutable_and_versioned(self) -> None:
        version = StrategyVersion(
            strategy_id="s1",
            version=2,
            definition="official docs → local → compare → verify",
            change_reason="experiment exp_1",
            source_experiments=("exp_1",),
        )
        with pytest.raises(ValidationError):  # frozen model
            version.version = 3  # type: ignore[misc]
        assert version.source_experiments == ("exp_1",)


class TestPromotion:
    def test_safety_regression_flag(self) -> None:
        decision = PromotionDecision(
            experiment_id="exp_1",
            hypothesis_id="hyp_1",
            decision="REJECT",
            reasons=("safety regression",),
            safety_regression=True,
        )
        assert decision.decision == "REJECT" and decision.safety_regression


class TestDecisionPreference:
    def test_preference_at_adaptation_point(self) -> None:
        preference = DecisionPreference(
            adaptation_point=AdaptationPoint.MODEL_SELECTION,
            context_key="research",
            preferred_option="provider/model-x",
            evidence_count=5,
            success_rate=0.9,
        )
        assert preference.active


class TestTrajectoryReproducibility:
    def test_new_fields_are_optional_additive(self) -> None:
        trajectory = _trajectory()
        assert trajectory.atlas_version is None
        assert trajectory.safety_events == ()

    def test_fields_round_trip(self) -> None:
        trajectory = _trajectory(
            atlas_version="0.1.0",
            git_commit="abc123",
            config_hash="hash",
            strategy_id="s1",
            strategy_version=2,
            model_version="m1",
            capability_snapshot_version="cap_1",
            safety_events=("tier2_block",),
            completion_confidence=0.8,
        )
        assert trajectory.strategy_version == 2

    async def test_store_persists_reproducibility_fields(self, memory_db: Database) -> None:
        from atlas.infra.clock import SystemClock
        from atlas.infra.ids import UuidGenerator
        from atlas.memory.trajectory_store import TrajectoryStore

        store = TrajectoryStore(db=memory_db, ids=UuidGenerator(), clock=SystemClock())
        trajectory = _trajectory(
            atlas_version="0.1.0",
            git_commit="abc123",
            strategy_version=2,
            safety_events=("tier2_block",),
            completion_confidence=0.8,
        )
        await store.save_trajectory(trajectory)
        loaded = await store.get_trajectory("traj_1")
        assert loaded is not None
        assert loaded.atlas_version == "0.1.0"
        assert loaded.git_commit == "abc123"
        assert loaded.strategy_version == 2
        assert loaded.safety_events == ("tier2_block",)
        assert loaded.completion_confidence == pytest.approx(0.8)
