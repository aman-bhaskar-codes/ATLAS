"""Orchestrator ↔ AgentSupervisor integration.

The contract under test is the fallback contract: delegation is an optional
strategy *inside* the one pipeline, and nothing about it may cost a user their
task. A supervisor that declines, explodes, or is absent entirely must all end
with the serial plan-then-reason path running normally.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from atlas.infra.cognition import Complexity, RiskLevel, TaskDomain, TaskIntent, VerificationResult
from atlas.infra.ids import CorrelationId, ExecutionId, TaskId
from atlas.infra.types import InboundEvent
from atlas.orchestration.agents.supervisor import SupervisionResult
from atlas.orchestration.agents.types import AgentRole, SubTask, SubTaskResult, SubTaskStatus, TaskDAG
from atlas.orchestration.orchestrator import Orchestrator
from atlas.orchestration.state import TaskState
from atlas.orchestration.types import Plan, PlanStep, TaskResult


class FrozenClock:
    def now(self) -> datetime:
        return datetime(2026, 1, 1, tzinfo=UTC)


class SeqIds:
    def __init__(self) -> None:
        self._n = 0

    def _next(self) -> str:
        self._n += 1
        return f"id{self._n}"

    def task_id(self) -> TaskId:
        return TaskId(self._next())

    def correlation_id(self) -> CorrelationId:
        return CorrelationId(self._next())

    def execution_id(self) -> ExecutionId:
        return ExecutionId(self._next())


class FakeExecStore:
    def __init__(self) -> None:
        self.states: list[str] = []

    async def create_task(self, **_kwargs: Any) -> None:
        return None

    async def update_task_state(self, *, state: str, **_kwargs: Any) -> None:
        self.states.append(state)


class FakeCancelStore:
    async def request_cancellation(self, _task_id: str) -> None:
        return None


class FakeUnderstanding:
    async def understand(self, request: str, _corr: str) -> TaskIntent:
        return TaskIntent(
            objective=request,
            domain=TaskDomain.RESEARCH,
            complexity=Complexity.COMPLEX,
            risk=RiskLevel.LOW,
        )


class FakeContextBuilder:
    async def build(self, _request: str, **_kwargs: Any) -> str:
        return "built context"


class FakeRegistry:
    def catalog(self) -> str:
        return "Available tools:"


class FakePlanner:
    """Records whether the serial path was taken at all."""

    def __init__(self) -> None:
        self.calls = 0

    async def plan(self, request: str, *_args: Any, **_kwargs: Any) -> Plan:
        self.calls += 1
        return Plan(goal=request, steps=(PlanStep(index=1, intent="do it"),))


class FakeReasoning:
    def __init__(self) -> None:
        self.calls = 0

    async def run(self, *, task_id: str, machine: Any, **_kwargs: Any) -> TaskResult:
        self.calls += 1
        machine.transition(TaskState.REASONING)
        machine.transition(TaskState.VALIDATING)
        machine.transition(TaskState.COMPLETED)
        return TaskResult(task_id=TaskId(task_id), ok=True, answer="serial answer", steps_taken=1)


class FakeEvents:
    def __init__(self) -> None:
        self.emitted: list[dict[str, Any]] = []

    async def emit(self, **kwargs: Any) -> None:
        self.emitted.append(kwargs)

    def kinds(self) -> list[str]:
        return [e["kind"] for e in self.emitted]


class StubSupervisor:
    """Returns a canned SupervisionResult, or raises."""

    def __init__(self, outcome: SupervisionResult | Exception) -> None:
        self._outcome = outcome
        self.calls = 0

    async def maybe_run(self, **_kwargs: Any) -> SupervisionResult:
        self.calls += 1
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


class FakeTrajectoryStore:
    def __init__(self) -> None:
        self.saved: list[Any] = []

    async def save_trajectory(self, trajectory: Any) -> Any:
        self.saved.append(trajectory)
        return "traj1"

    async def query_experiences(self, _query: Any) -> tuple[Any, ...]:
        return ()


def verified() -> VerificationResult:
    """What an independently-checked delegated run looks like."""
    return VerificationResult(passed=True, score=0.9, verifier="fake")


def delegated_result(*, ok: bool = True, verification: VerificationResult | None = None) -> SupervisionResult:
    status = SubTaskStatus.SUCCEEDED if ok else SubTaskStatus.FAILED
    results = (
        SubTaskResult(
            subtask_id="st1",
            role=AgentRole.RESEARCHER,
            status=status,
            output="found things" if ok else "",
            error=None if ok else "boom",
            steps_taken=3,
            tool_calls=2,
            model_calls=2,
            tokens_used=500,
        ),
    )
    return SupervisionResult(
        delegated=True,
        reason="two branches",
        answer="synthesized answer" if ok else None,
        results=results,
        dag=TaskDAG.build("goal", (SubTask(id="st1", objective="research"),)),
        verification=verification if ok else None,
    )


def build_orchestrator(
    *,
    supervisor: Any = None,
    planner: FakePlanner | None = None,
    reasoning: FakeReasoning | None = None,
    events: FakeEvents | None = None,
    trajectory_store: Any = None,
    exec_store: FakeExecStore | None = None,
) -> Orchestrator:
    return Orchestrator(
        ids=SeqIds(),
        clock=FrozenClock(),
        execution_store=exec_store or FakeExecStore(),
        cancellation_store=FakeCancelStore(),
        understanding=FakeUnderstanding(),  # type: ignore[arg-type]
        planner=planner or FakePlanner(),  # type: ignore[arg-type]
        context_builder=FakeContextBuilder(),  # type: ignore[arg-type]
        reasoning=reasoning or FakeReasoning(),  # type: ignore[arg-type]
        registry=FakeRegistry(),  # type: ignore[arg-type]
        events=events or FakeEvents(),  # type: ignore[arg-type]
        trajectory_store=trajectory_store,
        supervisor=supervisor,
    )


def event() -> InboundEvent:
    return InboundEvent(correlation_id="c1", source="cli", content="compare A and B and write it up")


async def test_no_supervisor_leaves_the_serial_path_untouched() -> None:
    planner, reasoning = FakePlanner(), FakeReasoning()
    orch = build_orchestrator(planner=planner, reasoning=reasoning)

    result = await orch.run(event())

    assert result.ok is True
    assert result.answer == "serial answer"
    assert planner.calls == 1
    assert reasoning.calls == 1


async def test_delegation_returns_the_synthesized_answer_and_skips_planning() -> None:
    planner, reasoning, events = FakePlanner(), FakeReasoning(), FakeEvents()
    sup = StubSupervisor(delegated_result(verification=verified()))
    orch = build_orchestrator(supervisor=sup, planner=planner, reasoning=reasoning, events=events)

    result = await orch.run(event())

    assert result.ok is True
    assert result.answer == "synthesized answer"
    assert result.verification_passed is True
    assert result.steps_taken == 3
    assert result.tool_calls == 2
    assert result.model_calls == 2
    assert result.tokens_used == 500
    assert sup.calls == 1
    # The whole point of deciding before planning: no plan was ever paid for.
    assert planner.calls == 0
    assert reasoning.calls == 0
    completed = [e for e in events.emitted if e["kind"] == "task.completed"]
    assert completed[-1]["strategy"] == "multi_agent"
    assert completed[-1]["subtasks"] == 1
    assert completed[-1]["outcome"] == "verified"


async def test_an_unverified_delegated_answer_says_so_in_the_answer_and_the_flag() -> None:
    """No interface renders `verification_passed`, so the caveat has to be visible
    in the text the user actually reads."""
    events = FakeEvents()
    orch = build_orchestrator(supervisor=StubSupervisor(delegated_result()), events=events)

    result = await orch.run(event())

    assert result.ok is True
    assert result.verification_passed is False
    assert result.answer is not None
    assert result.answer.startswith("[NOT VERIFIED")
    assert "synthesized answer" in result.answer  # the work is not thrown away
    assert [e for e in events.emitted if e["kind"] == "task.completed"][-1]["outcome"] == "uncertain"


async def test_a_rejected_delegated_answer_is_flagged_not_silently_completed() -> None:
    rejected = VerificationResult(passed=False, score=0.1, verifier="fake", failure_reason="criteria unmet")
    orch = build_orchestrator(supervisor=StubSupervisor(delegated_result(verification=rejected)))

    result = await orch.run(event())

    assert result.verification_passed is False
    assert result.verification_score == 0.1
    assert result.answer is not None
    assert result.answer.startswith("[FAILED VERIFICATION")
    assert "criteria unmet" in result.answer


async def test_a_declining_supervisor_falls_through_to_serial() -> None:
    planner, reasoning = FakePlanner(), FakeReasoning()
    sup = StubSupervisor(SupervisionResult(delegated=False, reason="too simple"))
    orch = build_orchestrator(supervisor=sup, planner=planner, reasoning=reasoning)

    result = await orch.run(event())

    assert result.answer == "serial answer"
    assert sup.calls == 1
    assert planner.calls == 1
    assert reasoning.calls == 1


async def test_a_crashing_supervisor_falls_through_to_serial() -> None:
    """A bug in the agents layer must not cost the user their task."""
    planner, reasoning = FakePlanner(), FakeReasoning()
    sup = StubSupervisor(RuntimeError("supervisor exploded"))
    orch = build_orchestrator(supervisor=sup, planner=planner, reasoning=reasoning)

    result = await orch.run(event())

    assert result.ok is True
    assert result.answer == "serial answer"
    assert planner.calls == 1


async def test_all_subtasks_failing_produces_a_failed_task_not_a_crash() -> None:
    exec_store = FakeExecStore()
    sup = StubSupervisor(delegated_result(ok=False))
    orch = build_orchestrator(supervisor=sup, exec_store=exec_store)

    result = await orch.run(event())

    assert result.ok is False
    assert result.answer is None
    assert result.error is not None and "failed=1" in result.error
    assert exec_store.states[-1] == TaskState.FAILED.value


async def test_delegated_state_path_ends_in_a_terminal_state() -> None:
    exec_store = FakeExecStore()
    orch = build_orchestrator(supervisor=StubSupervisor(delegated_result()), exec_store=exec_store)

    await orch.run(event())

    assert exec_store.states[-1] == TaskState.COMPLETED.value


async def test_delegated_trajectory_is_persisted_when_actions_exist() -> None:
    """Without this the learning loop would go blind on every delegated run."""
    from atlas.memory.trajectory import ActionRecord, ObservationRecord

    act = ActionRecord(step=1, kind="tool_call", tool="web", operation="search")
    obs = ObservationRecord(step=1, ok=True, content="found things")
    store = FakeTrajectoryStore()
    supervision = delegated_result()
    with_actions = SupervisionResult(
        delegated=True,
        reason=supervision.reason,
        answer=supervision.answer,
        results=(supervision.results[0].model_copy(update={"actions": (act,), "observations": (obs,)}),),
        dag=supervision.dag,
    )
    orch = build_orchestrator(supervisor=StubSupervisor(with_actions), trajectory_store=store)

    result = await orch.run(event())

    assert result.actions == (act,)
    assert result.observations == (obs,)
    assert len(store.saved) == 1


async def test_a_failing_trajectory_save_does_not_fail_the_delegated_task() -> None:
    from atlas.memory.trajectory import ActionRecord

    class BrokenStore(FakeTrajectoryStore):
        async def save_trajectory(self, trajectory: Any) -> Any:
            raise RuntimeError("disk full")

    supervision = delegated_result(verification=verified())
    with_actions = SupervisionResult(
        delegated=True,
        reason="r",
        answer="synthesized answer",
        results=(supervision.results[0].model_copy(update={"actions": (ActionRecord(step=1, kind="final_answer"),)}),),
        dag=supervision.dag,
        verification=verified(),
    )
    orch = build_orchestrator(supervisor=StubSupervisor(with_actions), trajectory_store=BrokenStore())

    result = await orch.run(event())

    assert result.ok is True
    assert result.answer == "synthesized answer"
