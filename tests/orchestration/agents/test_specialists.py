"""Specialist — role framing, per-subtask budgets, and failure containment.

A specialist is deliberately NOT a second runtime: it hands work to the shared
ReasoningLoop. These tests pin exactly what it contributes on top of that loop,
by capturing the kwargs it passes down.
"""

from __future__ import annotations

from typing import Any

from atlas.infra.cognition import TaskDomain
from atlas.infra.ids import CorrelationId, TaskId
from atlas.orchestration.agents.specialists import Specialist
from atlas.orchestration.agents.types import AgentRole, SubTask, SubTaskStatus
from atlas.orchestration.errors import CancellationError
from atlas.orchestration.managers.cancellation import CancellationToken
from atlas.orchestration.types import TaskResult

TASK = TaskId("t1")
CORR = CorrelationId("c1")


class RecordingLoop:
    """Captures the kwargs the specialist passes into ReasoningLoop.run."""

    def __init__(self, outcome: TaskResult | Exception | None = None) -> None:
        self._outcome = outcome
        self.calls: list[dict[str, Any]] = []

    async def run(self, **kwargs: Any) -> TaskResult:
        self.calls.append(kwargs)
        if isinstance(self._outcome, Exception):
            raise self._outcome
        if self._outcome is not None:
            return self._outcome
        return TaskResult(
            task_id=TASK,
            ok=True,
            answer="  the finding  ",
            steps_taken=3,
            tool_calls=1,
            model_calls=2,
            tokens_used=400,
            latency_ms=42,
        )


def st(**overrides: Any) -> SubTask:
    base: dict[str, Any] = {"id": "st1", "objective": "gather sources", "role": AgentRole.RESEARCHER}
    base.update(overrides)
    return SubTask(**base)


async def run_one(loop: RecordingLoop, subtask: SubTask, *, upstream: str = "", **kwargs: Any) -> Any:
    spec = Specialist(loop, **kwargs)  # type: ignore[arg-type]
    return await spec.run(
        subtask=subtask,
        parent_task_id=TASK,
        correlation_id=CORR,
        base_context="shared context text",
        upstream=upstream,
        token=CancellationToken(),
    )


async def test_a_successful_run_maps_onto_a_succeeded_result() -> None:
    result = await run_one(RecordingLoop(), st())

    assert result.status is SubTaskStatus.SUCCEEDED
    assert result.output == "the finding"  # stripped
    assert result.subtask_id == "st1"
    assert result.role is AgentRole.RESEARCHER
    assert (result.steps_taken, result.tool_calls, result.model_calls) == (3, 1, 2)
    assert result.tokens_used == 400
    assert result.latency_ms == 42


async def test_a_not_ok_task_result_becomes_a_failed_subtask() -> None:
    loop = RecordingLoop(TaskResult(task_id=TASK, ok=False, error="limit exhausted"))

    result = await run_one(loop, st())

    assert result.status is SubTaskStatus.FAILED
    assert result.error == "limit exhausted"


async def test_an_exception_is_contained_as_a_failed_subtask() -> None:
    """One specialist blowing up is data for the synthesizer, not a task sink."""
    result = await run_one(RecordingLoop(RuntimeError("gateway 500")), st())

    assert result.status is SubTaskStatus.FAILED
    assert result.error is not None and "gateway 500" in result.error


async def test_cancellation_propagates_rather_than_being_swallowed() -> None:
    loop = RecordingLoop(CancellationError("stop"))
    spec = Specialist(loop)  # type: ignore[arg-type]

    raised = False
    try:
        await spec.run(
            subtask=st(),
            parent_task_id=TASK,
            correlation_id=CORR,
            base_context="",
            upstream="",
            token=CancellationToken(),
        )
    except CancellationError:
        raised = True

    assert raised is True


async def test_the_subtask_budget_bounds_the_run_not_the_loop_default() -> None:
    loop = RecordingLoop()

    await run_one(loop, st(max_steps=3), max_tokens_per_subtask=7000, max_runtime_s=99.0)

    limits = loop.calls[0]["limits"]
    assert limits.max_steps == 3
    assert limits.max_tool_calls == 6  # 2x steps
    assert limits.max_tokens == 7000
    assert limits.max_runtime_s == 99.0


async def test_each_subtask_gets_its_own_state_machine() -> None:
    loop = RecordingLoop()
    spec = Specialist(loop)  # type: ignore[arg-type]

    for sid in ("st1", "st2"):
        await spec.run(
            subtask=st(id=sid),
            parent_task_id=TASK,
            correlation_id=CORR,
            base_context="",
            upstream="",
            token=CancellationToken(),
        )

    assert loop.calls[0]["machine"] is not loop.calls[1]["machine"]


async def test_the_role_brief_and_upstream_are_injected_into_the_context() -> None:
    loop = RecordingLoop()

    await run_one(loop, st(depends_on=("st0",)), upstream="--- st0 (analyst) ---\nprior finding")

    context = loop.calls[0]["context"]
    assert "RESEARCHER" in context
    assert "YOUR SUBTASK (st1)" in context
    assert "prior finding" in context
    assert "shared context text" in context


async def test_no_upstream_section_appears_when_there_is_no_upstream() -> None:
    loop = RecordingLoop()

    await run_one(loop, st())

    assert "SUBTASKS YOU DEPEND ON" not in loop.calls[0]["context"]


async def test_role_selects_the_verifier_domain() -> None:
    loop = RecordingLoop()

    await run_one(loop, st(role=AgentRole.CODER))

    assert loop.calls[0]["intent"].domain is TaskDomain.CODING


async def test_an_unknown_role_still_produces_a_usable_context() -> None:
    loop = RecordingLoop()

    await run_one(loop, st(role=AgentRole.GENERAL))

    assert "careful generalist" in loop.calls[0]["context"]
    assert loop.calls[0]["intent"].domain is TaskDomain.UNKNOWN


async def test_the_plan_stays_exploratory_so_critique_and_reflection_apply() -> None:
    loop = RecordingLoop()

    await run_one(loop, st(suggested_tools=("web_search",)))

    plan = loop.calls[0]["plan"]
    assert len(plan.steps) == 1
    assert plan.steps[0].tool is None  # NOT the fully-specified DAG fast path
    assert plan.steps[0].operation is None
    assert "web_search" in plan.steps[0].intent
    assert loop.calls[0]["caps"].needs_tools is True


async def test_risk_is_carried_into_the_capability_ceiling() -> None:
    from atlas.infra.cognition import RiskLevel

    loop = RecordingLoop()

    await run_one(loop, st(risk=RiskLevel.MEDIUM))

    assert loop.calls[0]["caps"].max_risk is RiskLevel.MEDIUM
    assert loop.calls[0]["plan"].risk is RiskLevel.MEDIUM


async def test_success_criteria_become_termination_conditions() -> None:
    loop = RecordingLoop()

    await run_one(loop, st(success_criteria=("three sources cited",)))

    assert loop.calls[0]["plan"].termination_conditions == ("three sources cited",)
    assert "three sources cited" in loop.calls[0]["context"]


async def test_trajectory_records_are_passed_upward() -> None:
    """The parent trajectory is the only thing the learning loop reads."""
    from atlas.memory.trajectory import ActionRecord, ObservationRecord

    act = ActionRecord(step=1, kind="tool_call", tool="web")
    obs = ObservationRecord(step=1, ok=True, content="hit")
    loop = RecordingLoop(TaskResult(task_id=TASK, ok=True, answer="a", actions=(act,), observations=(obs,)))

    result = await run_one(loop, st())

    assert result.actions == (act,)
    assert result.observations == (obs,)
