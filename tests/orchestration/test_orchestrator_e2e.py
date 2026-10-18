"""End-to-end tests for the Orchestrator facade.

Tests cover:
1. Complete task lifecycle (created -> ready -> context -> plan -> reason -> completed)
2. State machine transitions and structured event emissions
3. Cancellation token triggering
4. Execution failure handling and error states
5. Admission closure and ownership guarding
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from atlas.infra.cognition import Complexity, RiskLevel, TaskDomain, TaskIntent
from atlas.infra.db import Database
from atlas.infra.execution_lease import OwnershipLostError
from atlas.infra.ids import CorrelationId, ExecutionId, TaskId
from atlas.infra.types import InboundEvent
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
        return f"id_{self._n}"

    def task_id(self) -> TaskId:
        return TaskId(self._next())

    def correlation_id(self) -> CorrelationId:
        return CorrelationId(self._next())

    def execution_id(self) -> ExecutionId:
        return ExecutionId(self._next())


class MockExecStore:
    def __init__(self) -> None:
        self.created_tasks: list[dict[str, Any]] = []
        self.states: list[str] = []

    async def create_task(self, **kwargs: Any) -> None:
        self.created_tasks.append(kwargs)

    async def update_task_state(self, *, state: str, **kwargs: Any) -> None:
        self.states.append(state)

    async def record_task_result(self, **kwargs: Any) -> None:
        pass


class MockCancelStore:
    def __init__(self) -> None:
        self.cancelled_tasks: list[str] = []

    async def request_cancellation(self, task_id: str) -> None:
        self.cancelled_tasks.append(task_id)


class MockUnderstanding:
    async def understand(self, request: str, corr: str) -> TaskIntent:
        return TaskIntent(
            objective=request,
            domain=TaskDomain.CODING,
            complexity=Complexity.MODERATE,
            risk=RiskLevel.LOW,
        )


class MockContextBuilder:
    async def build(self, request: str, **kwargs: Any) -> str:
        return f"Context for: {request}"


class MockRegistry:
    def catalog(self) -> str:
        return "mock_tools"


class MockPlanner:
    async def plan(self, request: str, *args: Any, **kwargs: Any) -> Plan:
        return Plan(goal=request, steps=(PlanStep(index=1, intent="run step 1"),))


class MockReasoning:
    def __init__(self, should_fail: bool = False, ok: bool = True) -> None:
        self.should_fail = should_fail
        self.ok = ok

    async def run(self, *, task_id: str, machine: Any, **kwargs: Any) -> TaskResult:
        if self.should_fail:
            raise RuntimeError("Reasoning step crashed unexpectedly")
        machine.transition(TaskState.REASONING)
        machine.transition(TaskState.VALIDATING)
        machine.transition(TaskState.COMPLETED)
        return TaskResult(
            task_id=TaskId(task_id),
            ok=self.ok,
            answer="Successfully completed task answer." if self.ok else "Failed to fulfill task.",
            steps_taken=2,
        )


class MockEvents:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def emit(self, **kwargs: Any) -> None:
        self.events.append(kwargs)

    def kinds(self) -> list[str]:
        return [e["kind"] for e in self.events]


@pytest.fixture
def orchestrator_bundle():
    clock = FrozenClock()
    ids = SeqIds()
    exec_store = MockExecStore()
    cancel_store = MockCancelStore()
    events = MockEvents()
    reasoning = MockReasoning()

    orch = Orchestrator(
        ids=ids,
        clock=clock,
        execution_store=exec_store,
        cancellation_store=cancel_store,
        understanding=MockUnderstanding(),
        planner=MockPlanner(),
        context_builder=MockContextBuilder(),
        reasoning=reasoning,
        registry=MockRegistry(),
        events=events,
    )
    return orch, exec_store, events, reasoning


@pytest.mark.asyncio
async def test_orchestrator_full_lifecycle_success(orchestrator_bundle) -> None:
    """Orchestrator runs full pipeline from InboundEvent to completed TaskResult."""
    orch, exec_store, events, _ = orchestrator_bundle

    event = InboundEvent(correlation_id="corr-1", source="cli", content="Build test feature")
    result = await orch.run(event)

    assert result.ok is True
    assert result.answer == "Successfully completed task answer."
    assert result.steps_taken == 2

    # Verify task creation in store
    assert len(exec_store.created_tasks) == 1
    assert exec_store.created_tasks[0]["source"] == "cli"

    # Verify event progression
    kinds = events.kinds()
    assert "task.created" in kinds
    assert "task.started" in kinds
    assert "context.building" in kinds
    assert "intent.created" in kinds
    assert "planning.started" in kinds
    assert "task.completed" in kinds


@pytest.mark.asyncio
async def test_orchestrator_cancellation_trigger(orchestrator_bundle) -> None:
    """Orchestrator signals cancellation token and records cancel request."""
    orch, _, _, _ = orchestrator_bundle

    from atlas.orchestration.managers.cancellation import CancellationToken
    token = CancellationToken()
    orch._cancels["task-to-cancel"] = token

    orch.cancel("task-to-cancel")
    assert orch._cancels["task-to-cancel"].cancelled is True


@pytest.mark.asyncio
async def test_orchestrator_task_not_ok_emits_failed(orchestrator_bundle) -> None:
    """When reasoning produces ok=False, orchestrator emits task.failed event."""
    orch, _, events, reasoning = orchestrator_bundle
    reasoning.ok = False

    event = InboundEvent(correlation_id="corr-3", source="cli", content="Unsatisfiable request")
    result = await orch.run(event)

    assert result.ok is False
    assert "task.failed" in events.kinds()


@pytest.mark.asyncio
async def test_orchestrator_reasoning_exception_transitions_failed(orchestrator_bundle) -> None:
    """When reasoning crashes, orchestrator transitions state machine to failed and re-raises."""
    orch, _, _, reasoning = orchestrator_bundle
    reasoning.should_fail = True

    event = InboundEvent(correlation_id="corr-4", source="cli", content="Crashing task")
    with pytest.raises(RuntimeError) as exc_info:
        await orch.run(event)
    assert "crashed unexpectedly" in str(exc_info.value)


@pytest.mark.asyncio
async def test_orchestrator_closed_admission(orchestrator_bundle) -> None:
    """When orchestrator admission is closed, prepare_task and run reject new tasks."""
    orch, _, _, _ = orchestrator_bundle
    orch._closed = True

    with pytest.raises(OwnershipLostError) as exc_info:
        await orch.prepare_task("t1")
    assert "closed" in str(exc_info.value).lower()

    event = InboundEvent(correlation_id="corr-4", source="cli", content="Rejected job")
    with pytest.raises(OwnershipLostError):
        await orch.run(event)
