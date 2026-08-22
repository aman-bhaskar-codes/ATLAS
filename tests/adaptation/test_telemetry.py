"""Tests for cognitive telemetry, confidence calibration, uncertainty-driven
behavior and adaptive routing (Prompt 4 §32-§37)."""

from __future__ import annotations

import random

import pytest

from atlas.adaptation import (
    AdaptiveRouter,
    ArmKind,
    CalibrationTracker,
    CognitiveTelemetryCollector,
    DecisionQuality,
    RoutingStatsStore,
    TelemetryStore,
    TrajectoryEvaluation,
    UncertaintyAction,
    uncertainty_actions,
)
from atlas.infra.db import Database


class TestUncertaintyBehavior:
    def test_very_low_confidence_triggers_escalation_path(self) -> None:
        """§34: low confidence changes behavior, not just a UI number."""
        actions = uncertainty_actions(0.2)
        assert UncertaintyAction.GATHER_MORE_EVIDENCE in actions
        assert UncertaintyAction.DEEPER_REASONING in actions
        assert UncertaintyAction.USER_CLARIFICATION in actions

    def test_mid_confidence_tries_alternatives_and_verifies(self) -> None:
        assert uncertainty_actions(0.4) == (
            UncertaintyAction.GATHER_MORE_EVIDENCE,
            UncertaintyAction.ALTERNATE_MODEL,
            UncertaintyAction.VERIFY,
        )
        assert uncertainty_actions(0.6) == (UncertaintyAction.ALTERNATE_TOOL, UncertaintyAction.VERIFY)

    def test_high_confidence_no_extra_behavior(self) -> None:
        assert uncertainty_actions(0.9) == ()


class TestCalibration:
    async def test_poor_calibration_detected(self, memory_db: Database) -> None:
        """§33: says 0.9 confidence but succeeds ~60% of the time."""
        tracker = CalibrationTracker(db=memory_db)
        for i in range(20):
            await tracker.record(0.9, i < 12, trajectory_id=f"t{i}")
        report = await tracker.report()
        assert report.n_records == 20
        assert report.calibration_error > 0.25  # badly overconfident
        filled = [b for b in report.reliability_curve if b.n > 0]
        assert len(filled) == 1  # all records in one bucket
        assert filled[0].success_rate == 12 / 20
        assert await tracker.escalation_adjustment() == "raise"

    async def test_well_calibrated_keeps_escalation(self, memory_db: Database) -> None:
        tracker = CalibrationTracker(db=memory_db)
        # confidence matches realized success: 0.5 conf succeeds half the time
        for i in range(20):
            await tracker.record(0.5, i % 2 == 0)
        report = await tracker.report()
        assert report.calibration_error < 0.05
        assert await tracker.escalation_adjustment() == "keep"

    async def test_thin_evidence_never_adjusts(self, memory_db: Database) -> None:
        tracker = CalibrationTracker(db=memory_db)
        for _ in range(3):
            await tracker.record(0.95, False)
        assert await tracker.escalation_adjustment() == "keep"

    async def test_rejects_out_of_range_confidence(self, memory_db: Database) -> None:
        tracker = CalibrationTracker(db=memory_db)

        with pytest.raises(ValueError, match="confidence"):
            await tracker.record(1.5, True)


class TestCognitiveTelemetry:
    async def test_collect_and_persist(self, memory_db: Database) -> None:
        """§32: every dimension linked to a trajectory, derived from measured
        artifacts only."""
        evaluation = TrajectoryEvaluation(
            trajectory_id="t1",
            planning_quality=0.8,
            tool_selection=0.6,
            memory_usefulness=0.7,
            verification=0.9,
            efficiency=0.5,
        )
        qualities = (
            DecisionQuality(trajectory_id="t1", dimension="model_selection", score=1.0),
            DecisionQuality(trajectory_id="t1", dimension="retrieval", score=0.4),
            DecisionQuality(trajectory_id="t1", dimension="recovery", score=0.2),
        )
        telemetry = CognitiveTelemetryCollector().collect(
            "t1", evaluation=evaluation, qualities=qualities, strategy_transfer=0.75, confidence_calibration=0.3
        )
        assert telemetry.planning_quality == 0.8
        assert telemetry.tool_selection_accuracy == 0.6  # evaluation wins over absent quality
        assert telemetry.model_selection_quality == 1.0
        assert telemetry.retrieval_usefulness == 0.4
        assert telemetry.memory_usefulness == 0.7
        assert telemetry.verification_quality == 0.9
        assert telemetry.recovery_quality == 0.2
        assert telemetry.research_efficiency == 0.5
        assert telemetry.strategy_transfer == 0.75
        assert telemetry.confidence_calibration == 0.3

        store = TelemetryStore(db=memory_db)
        await store.save(telemetry)
        saved = await store.for_trajectory("t1")
        assert len(saved) == 1 and saved[0].trajectory_id == "t1"
        averages = await store.averages()
        assert averages["planning_quality"] == 0.8

    async def test_missing_dimensions_stay_none(self, memory_db: Database) -> None:
        telemetry = CognitiveTelemetryCollector().collect("t2")
        assert telemetry.planning_quality is None
        assert telemetry.model_selection_quality is None


class TestAdaptiveRouting:
    async def _stats(self, db: Database, arm: str, *, runs: int, successes: int) -> None:
        store = RoutingStatsStore(db=db)
        for i in range(runs):
            await store.record_outcome(
                ArmKind.MODEL, arm, "coding", success=i < successes, quality=0.5, latency_ms=10, cost_usd=0.01
            )

    async def test_no_learned_routing_before_evidence(self, memory_db: Database) -> None:
        """§35: before enough data, static capability-based routing stays."""
        await self._stats(memory_db, "model_a", runs=2, successes=2)
        router = AdaptiveRouter(store=RoutingStatsStore(db=memory_db))
        assert await router.choose(ArmKind.MODEL, "coding", ("model_a", "model_b")) is None

    async def test_exploits_best_arm_with_evidence(self, memory_db: Database) -> None:
        await self._stats(memory_db, "model_a", runs=6, successes=2)
        await self._stats(memory_db, "model_b", runs=6, successes=6)
        router = AdaptiveRouter(store=RoutingStatsStore(db=memory_db), exploration_rate=0.0, rng=random.Random(1))
        choice = await router.choose(ArmKind.MODEL, "coding", ("model_a", "model_b"))
        assert choice is not None and choice.arm == "model_b" and not choice.explored

    async def test_controlled_exploration_happens(self, memory_db: Database) -> None:
        """§36: exploration rate 1.0 always picks a non-best arm."""
        await self._stats(memory_db, "model_a", runs=6, successes=2)
        await self._stats(memory_db, "model_b", runs=6, successes=6)
        router = AdaptiveRouter(store=RoutingStatsStore(db=memory_db), exploration_rate=1.0, rng=random.Random(1))
        choice = await router.choose(ArmKind.MODEL, "coding", ("model_a", "model_b"))
        assert choice is not None and choice.explored and choice.arm == "model_a"

    async def test_exploration_is_measurable_and_reversible(self, memory_db: Database) -> None:
        """§37: exploration runs are counted; stats can be reset."""
        store = RoutingStatsStore(db=memory_db)
        await store.record_outcome(ArmKind.STRATEGY, "s2", "research", success=True, explored=True)
        await store.record_outcome(ArmKind.STRATEGY, "s2", "research", success=True, explored=False)
        stats = await store.get(ArmKind.STRATEGY, "s2", "research")
        assert stats is not None and stats.runs == 2 and stats.exploration_runs == 1
        assert stats.success_rate == 1.0

        await store.reset(ArmKind.STRATEGY, "research")
        assert await store.get(ArmKind.STRATEGY, "s2", "research") is None

    async def test_single_arm_no_exploration(self, memory_db: Database) -> None:
        await self._stats(memory_db, "model_a", runs=6, successes=6)
        router = AdaptiveRouter(store=RoutingStatsStore(db=memory_db), exploration_rate=1.0, rng=random.Random(1))
        choice = await router.choose(ArmKind.MODEL, "coding", ("model_a",))
        assert choice is not None and choice.arm == "model_a" and not choice.explored
