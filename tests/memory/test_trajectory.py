"""Phase 2 trajectory store tests — verify trajectory, decision, failure, experience CRUD."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio

from atlas.infra.clock import SystemClock
from atlas.infra.db import Database
from atlas.infra.ids import UuidGenerator
from atlas.memory.trajectory import (
    ActionRecord,
    DecisionOutcome,
    DecisionPoint,
    DecisionTrace,
    Experience,
    ExperienceCategory,
    ExperienceQuery,
    FailureCategory,
    FailureRecord,
    ObservationRecord,
    Trajectory,
    TrajectoryQuery,
)
from atlas.memory.trajectory_store import TrajectoryStore


@pytest_asyncio.fixture
async def db(tmp_path: Path):
    d = Database(tmp_path / "test.db")
    await d.start()
    yield d
    await d.stop()


@pytest_asyncio.fixture
async def store(db: Database) -> TrajectoryStore:
    return TrajectoryStore(db=db, ids=UuidGenerator(), clock=SystemClock())


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Trajectory Tests
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _make_trajectory(task_id: str = "task-1", success: bool = True) -> Trajectory:
    now = datetime.now(UTC)
    return Trajectory(
        id=f"traj-{task_id}",
        task_id=task_id,
        correlation_id="corr-1",
        request="Do something",
        goal="Complete the task",
        plan_steps=("Step 1", "Step 2"),
        risk_level="low",
        plan_confidence=0.8,
        actions=(
            ActionRecord(step=1, kind="tool_call", tool="test_tool", operation="read", args={"arg": "value"}),
            ActionRecord(step=2, kind="final_answer", final_text="Done!"),
        ),
        observations=(ObservationRecord(step=1, ok=True, content="Success"),),
        decision_traces=(),
        failure_records=(),
        replan_count=0,
        verification_passed=True,
        verification_score=0.95,
        success=success,
        answer="Completed successfully" if success else None,
        error=None if success else "Failed",
        steps_taken=2,
        latency_ms=1000,
        tokens_used=500,
        cost_usd=0.01,
        model_calls=2,
        tool_calls=1,
        created_ts=now,
        completed_ts=now,
    )


@pytest.mark.asyncio
async def test_save_and_get_trajectory(store: TrajectoryStore) -> None:
    """Save trajectory and retrieve by ID."""
    trajectory = _make_trajectory()

    # Save
    traj_id = await store.save_trajectory(trajectory)
    assert traj_id == trajectory.id

    # Retrieve
    loaded = await store.get_trajectory(traj_id)
    assert loaded is not None
    assert loaded.id == trajectory.id
    assert loaded.task_id == trajectory.task_id
    assert loaded.success == trajectory.success
    assert len(loaded.actions) == 2
    assert len(loaded.observations) == 1


@pytest.mark.asyncio
async def test_get_trajectory_by_task(store: TrajectoryStore) -> None:
    """Get trajectory by task_id (one-to-one)."""
    trajectory = _make_trajectory(task_id="task-unique")

    await store.save_trajectory(trajectory)

    loaded = await store.get_trajectory_by_task("task-unique")
    assert loaded is not None
    assert loaded.task_id == "task-unique"


@pytest.mark.asyncio
async def test_query_trajectories_with_filters(store: TrajectoryStore) -> None:
    """Query trajectories with multiple filters."""
    # Save multiple trajectories
    await store.save_trajectory(_make_trajectory(task_id="task-1", success=True))
    await store.save_trajectory(_make_trajectory(task_id="task-2", success=False))
    await store.save_trajectory(_make_trajectory(task_id="task-3", success=True))

    # Query successful only
    query = TrajectoryQuery(success=True, limit=10)
    results = await store.query_trajectories(query)
    assert len(results) == 2
    assert all(t.success for t in results)

    # Query failed only
    query = TrajectoryQuery(success=False, limit=10)
    results = await store.query_trajectories(query)
    assert len(results) == 1
    assert not results[0].success


@pytest.mark.asyncio
async def test_get_recent_trajectories(store: TrajectoryStore) -> None:
    """Get most recent trajectories ordered by completion time."""
    for i in range(5):
        await store.save_trajectory(_make_trajectory(task_id=f"task-{i}"))

    recent = await store.get_recent_trajectories(limit=3)
    assert len(recent) == 3


@pytest.mark.asyncio
async def test_get_failed_trajectories(store: TrajectoryStore) -> None:
    """Get only failed trajectories."""
    await store.save_trajectory(_make_trajectory(task_id="task-pass", success=True))
    await store.save_trajectory(_make_trajectory(task_id="task-fail", success=False))

    failed = await store.get_failed_trajectories(limit=10)
    assert len(failed) == 1
    assert failed[0].task_id == "task-fail"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Decision Trace Tests
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _make_decision_trace(task_id: str = "task-1") -> DecisionTrace:
    return DecisionTrace(
        id="dec-1",
        task_id=task_id,
        correlation_id="corr-1",
        ts=datetime.now(UTC),
        decision_point=DecisionPoint.MODEL_SELECTION,
        options_considered=("gpt-4", "claude-3.5-sonnet"),
        chosen_option="claude-3.5-sonnet",
        rationale="Better for this task",
        context={"complexity": "high"},
        outcome=DecisionOutcome.SUCCESS,
        outcome_detail="Worked well",
        confidence=0.9,
        latency_ms=50,
        cost_usd=0.005,
    )


@pytest.mark.asyncio
async def test_save_and_get_decision_traces(store: TrajectoryStore) -> None:
    """Save decision trace and query."""
    # FK parent: decision traces / failure records reference trajectories(task_id)
    await store.save_trajectory(_make_trajectory())

    trace = _make_decision_trace()

    trace_id = await store.save_decision_trace(trace)
    assert trace_id == trace.id

    traces = await store.get_decision_traces(task_id="task-1", limit=10)
    assert len(traces) == 1
    assert traces[0].chosen_option == "claude-3.5-sonnet"


@pytest.mark.asyncio
async def test_update_decision_outcome(store: TrajectoryStore) -> None:
    """Update decision outcome after observing result."""
    # FK parent: decision traces / failure records reference trajectories(task_id)
    await store.save_trajectory(_make_trajectory())

    trace = _make_decision_trace()
    await store.save_decision_trace(trace)

    # Update outcome
    await store.update_decision_outcome(
        trace.id,
        DecisionOutcome.FAILURE,
        "Actually didn't work",
    )

    traces = await store.get_decision_traces(task_id="task-1", limit=10)
    assert traces[0].outcome == DecisionOutcome.FAILURE
    assert traces[0].outcome_detail == "Actually didn't work"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Failure Record Tests
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _make_failure_record(task_id: str = "task-1", recovered: bool = False) -> FailureRecord:
    return FailureRecord(
        id="fail-1",
        task_id=task_id,
        correlation_id="corr-1",
        ts=datetime.now(UTC),
        category=FailureCategory.TOOL_ERROR,
        step=3,
        component="tool_dispatcher",
        error_message="Connection timeout",
        context={"timeout_ms": 5000},
        recovered=recovered,
        recovery_method="retry" if recovered else None,
        recovery_succeeded=recovered,
        similar_failure_ids=(),
        mitigation_suggested="Increase timeout",
        mitigation_applied=False,
    )


@pytest.mark.asyncio
async def test_save_and_get_failure_records(store: TrajectoryStore) -> None:
    """Save failure record and query."""
    await store.save_trajectory(_make_trajectory())  # FK parent
    failure = _make_failure_record()

    fail_id = await store.save_failure_record(failure)
    assert fail_id == failure.id

    failures = await store.get_failure_records(task_id="task-1", limit=10)
    assert len(failures) == 1
    assert failures[0].category == FailureCategory.TOOL_ERROR


@pytest.mark.asyncio
async def test_get_failure_patterns(store: TrajectoryStore) -> None:
    """Identify recurring failure patterns."""
    # Create multiple similar failures
    for i in range(5):
        await store.save_trajectory(_make_trajectory(task_id=f"task-{i}"))  # FK parent
        f = _make_failure_record(task_id=f"task-{i}")
        f = f.model_copy(update={"id": f"fail-{i}"})
        await store.save_failure_record(f)

    patterns = await store.get_failure_patterns(
        category=FailureCategory.TOOL_ERROR,
        min_occurrences=3,
    )

    assert len(patterns) >= 1
    assert patterns[0]["occurrence_count"] >= 3


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Experience Tests
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _make_experience(trajectory_id: str = "traj-1") -> Experience:
    return Experience(
        id="exp-1",
        trajectory_id=trajectory_id,
        task_id="task-1",
        correlation_id="corr-1",
        category=ExperienceCategory.TOOL_USAGE,
        lesson_text="Use --verbose flag for better debugging",
        applicability_context="When debugging tool failures",
        confidence=0.8,
        supporting_actions=(1, 2),
        supporting_observations=(1,),
        counter_examples=(),
        reuse_count=0,
        success_rate=0.0,
        avg_improvement_ms=0,
        avg_cost_savings_usd=0.0,
        extracted_ts=datetime.now(UTC),
        last_applied_ts=None,
        superseded_by=None,
    )


@pytest.mark.asyncio
async def test_save_and_get_experience(store: TrajectoryStore) -> None:
    """Save experience and retrieve."""
    await store.save_trajectory(_make_trajectory())  # FK parent (traj-task-1→use matching id)
    experience = _make_experience(trajectory_id="traj-task-1")

    exp_id = await store.save_experience(experience)
    assert exp_id == experience.id

    loaded = await store.get_experience(exp_id)
    assert loaded is not None
    assert loaded.lesson_text == experience.lesson_text


@pytest.mark.asyncio
async def test_query_experiences(store: TrajectoryStore) -> None:
    """Query experiences with filters."""
    # Save multiple experiences
    for i in range(3):
        await store.save_trajectory(_make_trajectory(task_id=str(i)))  # FK parent (traj-i)
        exp = _make_experience(trajectory_id=f"traj-{i}")
        exp = exp.model_copy(update={"id": f"exp-{i}", "confidence": 0.5 + i * 0.2})
        await store.save_experience(exp)

    # Query with min confidence
    query = ExperienceQuery(min_confidence=0.8, limit=10)
    results = await store.query_experiences(query)

    assert all(e.confidence >= 0.8 for e in results)


@pytest.mark.asyncio
async def test_record_experience_application(store: TrajectoryStore) -> None:
    """Record experience application and update stats."""
    await store.save_trajectory(_make_trajectory())  # FK parent
    experience = _make_experience(trajectory_id="traj-task-1")
    await store.save_experience(experience)

    # Record application
    await store.record_experience_application(
        experience_id=experience.id,
        task_id="task-2",
        success=True,
        improvement_ms=100,
        cost_savings_usd=0.002,
    )

    # Check updated stats
    loaded = await store.get_experience(experience.id)
    assert loaded is not None
    assert loaded.reuse_count == 1
    assert loaded.success_rate == 1.0
    assert loaded.avg_improvement_ms == 100


@pytest.mark.asyncio
async def test_supersede_experience(store: TrajectoryStore) -> None:
    """Mark experience as superseded."""
    await store.save_trajectory(_make_trajectory())  # FK parent
    old_exp = _make_experience(trajectory_id="traj-task-1")
    old_exp = old_exp.model_copy(update={"id": "exp-old"})
    await store.save_experience(old_exp)

    new_exp = _make_experience(trajectory_id="traj-task-1")
    new_exp = new_exp.model_copy(update={"id": "exp-new"})
    await store.save_experience(new_exp)

    # Supersede old with new
    await store.supersede_experience("exp-old", "exp-new")

    loaded = await store.get_experience("exp-old")
    assert loaded is not None
    assert loaded.superseded_by == "exp-new"
