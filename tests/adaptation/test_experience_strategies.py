"""Tests for experience extraction/validation, skill lifecycle and strategy
versioning (Prompt 4 §9-§14)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from atlas.adaptation import (
    ExperienceExtractor,
    ExperienceStore,
    ExperienceValidator,
    SkillLifecycleStore,
    SkillState,
    StrategyPerformanceTracker,
    StrategyVersion,
    StrategyVersionStore,
)
from atlas.adaptation.domain import StrategyPerformance
from atlas.infra.db import Database
from atlas.memory.trajectory import (
    ActionRecord,
    FailureCategory,
    FailureRecord,
    ObservationRecord,
    Trajectory,
)


def _trajectory(**overrides: object) -> Trajectory:
    now = datetime.now(UTC)
    base: dict[str, object] = {
        "id": "traj_x1",
        "task_id": "task_1",
        "correlation_id": "corr_1",
        "request": "fix the build",
        "goal": "fix the failing build",
        "plan_steps": ("diagnose", "fix", "verify"),
        "risk_level": "low",
        "plan_confidence": 0.8,
        "success": True,
        "steps_taken": 3,
        "latency_ms": 100,
        "tokens_used": 100,
        "cost_usd": 0.001,
        "model_calls": 1,
        "tool_calls": 2,
        "created_ts": now,
        "completed_ts": now,
        "actions": (
            ActionRecord(step=1, kind="tool_call", tool="shell"),
            ActionRecord(step=2, kind="tool_call", tool="filesystem"),
        ),
        "observations": (
            ObservationRecord(step=1, ok=True, content="error found"),
            ObservationRecord(step=2, ok=False, error="wrong path"),
        ),
    }
    base.update(overrides)
    return Trajectory(**base)  # type: ignore[arg-type]


class TestExperienceExtractor:
    def test_structured_fields_from_trajectory(self) -> None:
        experience = ExperienceExtractor().extract(_trajectory())
        assert experience.problem_pattern.startswith("fix the failing build")
        assert "1:shell" in experience.successful_action_sequence
        assert "2:filesystem" in experience.failed_action_sequence
        assert experience.lesson_candidate  # candidate only, never fact
        assert not experience.validated

    def test_recovery_pattern_from_failure_records(self) -> None:
        record = FailureRecord(
            id="fr1",
            task_id="task_1",
            correlation_id="corr_1",
            ts=datetime.now(UTC),
            category=FailureCategory.TOOL_ERROR,
            step=2,
            component="tool_dispatcher",
            error_message="path missing",
            recovered=True,
            recovery_method="replan",
            recovery_succeeded=True,
        )
        experience = ExperienceExtractor().extract(_trajectory(), failure_records=(record,))
        assert experience.recovery_pattern == "replan"


class TestExperienceValidator:
    def test_single_success_is_not_reusable(self) -> None:
        """§10: one successful task must NOT become a permanent skill."""
        store_like = ExperienceExtractor().extract(_trajectory())
        result = ExperienceValidator().validate_pattern((store_like,))
        assert not result.valid
        assert any("repeated 1" in r for r in result.reasons)

    def test_repeated_pattern_with_success_validates(self) -> None:
        extractor = ExperienceExtractor()
        experiences = tuple(extractor.extract(_trajectory(id=f"t{i}")) for i in range(3))
        result = ExperienceValidator().validate_pattern(experiences, application_successes=3, application_attempts=3)
        assert result.valid

    def test_low_success_rate_blocks_validation(self) -> None:
        extractor = ExperienceExtractor()
        experiences = tuple(extractor.extract(_trajectory(id=f"t{i}")) for i in range(3))
        result = ExperienceValidator().validate_pattern(experiences, application_successes=1, application_attempts=3)
        assert not result.valid


class TestExperienceStore:
    async def test_round_trip_and_validation_flag(self, memory_db: Database) -> None:
        store = ExperienceStore(db=memory_db)
        experience = ExperienceExtractor().extract(_trajectory())
        await store.save(experience)
        loaded = await store.for_pattern(experience.problem_pattern)
        assert len(loaded) == 1
        assert loaded[0].successful_action_sequence == experience.successful_action_sequence
        await store.mark_validated(experience.experience_id)
        again = await store.for_pattern(experience.problem_pattern)
        assert again[0].validated


class TestSkillLifecycle:
    async def test_full_lifecycle_with_evidence_gates(self, memory_db: Database) -> None:
        lifecycle = SkillLifecycleStore(db=memory_db)
        assert await lifecycle.ensure("debug_python_deps") is SkillState.EXPERIMENTAL

        # Gate refuses without evidence.
        assert not await lifecycle.transition("debug_python_deps", SkillState.VALIDATED)

        for _ in range(3):
            await lifecycle.record_application("debug_python_deps", success=True)
        assert await lifecycle.transition("debug_python_deps", SkillState.VALIDATED, reason="3/3 success")
        assert await lifecycle.transition("debug_python_deps", SkillState.ACTIVE)

        state, _applications, successes = await lifecycle.state("debug_python_deps")
        assert state is SkillState.ACTIVE and successes == 3

    async def test_active_skill_deprecates_only_on_measured_decline(self, memory_db: Database) -> None:
        lifecycle = SkillLifecycleStore(db=memory_db)
        await lifecycle.ensure("flaky_skill")
        for _ in range(3):
            await lifecycle.record_application("flaky_skill", success=True)
        await lifecycle.transition("flaky_skill", SkillState.VALIDATED)
        await lifecycle.transition("flaky_skill", SkillState.ACTIVE)
        # Two extra failures: 3/5 = 0.6 — still above 0.3 → gate refuses.
        await lifecycle.record_application("flaky_skill", success=False)
        await lifecycle.record_application("flaky_skill", success=False)
        assert not await lifecycle.transition("flaky_skill", SkillState.DEPRECATED)
        # More failures drag the rate to ≤0.3 with ≥5 applications (3/10).
        for _ in range(5):
            await lifecycle.record_application("flaky_skill", success=False)
        assert await lifecycle.transition("flaky_skill", SkillState.DEPRECATED, reason="success rate collapse")

    async def test_no_transition_out_of_deprecated(self, memory_db: Database) -> None:
        lifecycle = SkillLifecycleStore(db=memory_db)
        await lifecycle.ensure("dead_skill")
        await lifecycle.transition("dead_skill", SkillState.DEPRECATED)
        assert not await lifecycle.transition("dead_skill", SkillState.VALIDATED)


class TestStrategyVersionStore:
    async def test_versions_are_immutable(self, memory_db: Database) -> None:
        store = StrategyVersionStore(db=memory_db)
        v1 = StrategyVersion(
            strategy_id="s_build",
            version=1,
            definition="diagnose → fix → verify",
            change_reason="initial",
        )
        await store.save_version(v1)
        with pytest.raises(ValueError, match="immutable"):
            await store.save_version(v1)

    async def test_next_version_and_latest(self, memory_db: Database) -> None:
        store = StrategyVersionStore(db=memory_db)
        assert await store.next_version("s_build") == 1
        await store.save_version(StrategyVersion(strategy_id="s_build", version=1, definition="a"))
        assert await store.next_version("s_build") == 2
        await store.save_version(
            StrategyVersion(
                strategy_id="s_build",
                version=2,
                definition="a improved",
                change_reason="exp_1",
                source_experiments=("exp_1",),
            )
        )
        latest = await store.latest("s_build")
        assert latest is not None and latest.version == 2
        assert latest.source_experiments == ("exp_1",)
        all_versions = await store.versions("s_build")
        assert [v.version for v in all_versions] == [1, 2]


class TestStrategyPerformance:
    async def test_running_statistics(self, memory_db: Database) -> None:
        tracker = StrategyPerformanceTracker(db=memory_db)
        await tracker.record_outcome("s_build", 1, success=True, quality_score=0.9, latency_ms=100, cost_usd=0.02)
        await tracker.record_outcome("s_build", 1, success=False, quality_score=0.5, latency_ms=200, cost_usd=0.04)
        performance = await tracker.get("s_build", 1)
        assert performance.runs == 2
        assert performance.success_rate == pytest.approx(0.5)
        assert performance.latency_ms_avg == pytest.approx(150.0)
        assert performance.cost_usd_avg == pytest.approx(0.03)

    async def test_generalization_set_by_gate(self, memory_db: Database) -> None:
        tracker = StrategyPerformanceTracker(db=memory_db)
        await tracker.record_outcome("s_build", 1, success=True)
        await tracker.set_generalization("s_build", 1, 0.85)
        performance = await tracker.get("s_build", 1)
        assert performance.generalization == pytest.approx(0.85)

    async def test_missing_row_returns_zero_performance(self, memory_db: Database) -> None:
        tracker = StrategyPerformanceTracker(db=memory_db)
        performance = await tracker.get("ghost", 1)
        assert isinstance(performance, StrategyPerformance)
        assert performance.runs == 0
