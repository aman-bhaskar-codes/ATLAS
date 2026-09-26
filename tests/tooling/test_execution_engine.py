"""End-to-end execution tests (§105-§107/§121): RoutePlan → governed funnel →
result, with persistence and events."""

from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio

from atlas.tooling.execution import ExecutionRequest, TerminalOutcome
from tests.tooling.execution_helpers import Harness, ProbeTool, plan_of, step


@pytest_asyncio.fixture
async def harness(memory_db: Any) -> Any:
    return Harness(memory_db)


@pytest.mark.asyncio
async def test_single_step_executes_through_safety_engine(harness: Any) -> None:
    """§105: RoutePlan → ExecutionEngine → ToolingExecutor → SafetyEngine →
    real tool → result, with a full persistence + event trail."""

    probe = harness.register_tool(ProbeTool(), operations=("probe",))
    events: list[Any] = []

    class Bus:
        async def publish(self, topic: str, event: Any) -> None:
            events.append(event)

    engine = await harness.build_engine(bus=Bus())
    result = await engine.start(
        ExecutionRequest(
            plan=plan_of(step("s1", probe, "probe"), plan_id="p1"),
            task_id="t1",
            correlation_id="c1",
        )
    )

    assert result.outcome == TerminalOutcome.SUCCESS
    assert result.completed_steps == ("s1",)
    assert result.outputs == {"s1": "probe-1"}
    assert result.attempts == 1
    assert harness.tools["audit_probe"].calls == 1

    # §48/§83: persisted + evented with sequences.
    run = await engine.status(result.run_id)
    assert run is not None and run.status.value == "COMPLETED"
    assert run.outcome == TerminalOutcome.SUCCESS
    kinds = [e.kind for e in events]
    assert "execution.started" in kinds and "step.started" in kinds and "step.completed" in kinds
    sequences = [e.sequence for e in events]
    assert sequences == sorted(sequences) and len(set(sequences)) == len(sequences)


@pytest.mark.asyncio
async def test_three_step_serial_route_with_checkpoints(harness: Any) -> None:
    """§106: A → B → C serial route, checkpointed at boundaries."""
    probe = harness.register_tool(ProbeTool(), operations=("probe",))
    engine = await harness.build_engine()
    result = await engine.start(
        ExecutionRequest(
            plan=plan_of(
                step("a", probe, "probe"),
                step("b", probe, "probe"),
                step("c", probe, "probe"),
                plan_id="serial",
            ),
            task_id="t2",
            correlation_id="c2",
        )
    )
    assert result.outcome == TerminalOutcome.SUCCESS
    assert result.completed_steps == ("a", "b", "c")
    assert result.outputs["c"] == "probe-3"
    assert harness.tools["audit_probe"].calls == 3


@pytest.mark.asyncio
async def test_invalid_plan_is_never_executed(harness: Any) -> None:
    """§98/§118: an invalid plan is rejected with a typed outcome — no step runs."""
    probe = harness.register_tool(ProbeTool(), operations=("probe",))
    engine = await harness.build_engine()
    bad_plan = plan_of(
        step("s1", probe, "probe"),
        step("s2", probe, "probe", depends_on=("missing-step",)),
        plan_id="bad",
    )
    result = await engine.start(ExecutionRequest(plan=bad_plan, task_id="t3", correlation_id="c3"))
    assert result.outcome == TerminalOutcome.BLOCKED
    assert "unknown" in (result.summary or "")
    assert harness.tools["audit_probe"].calls == 0


@pytest.mark.asyncio
async def test_cyclic_plan_is_rejected(harness: Any) -> None:
    """§62: dependency cycles are detected before execution."""
    probe = harness.register_tool(ProbeTool(), operations=("probe",))
    engine = await harness.build_engine()
    cyclic = plan_of(
        step("a", probe, "probe", depends_on=("b",)),
        step("b", probe, "probe", depends_on=("a",)),
        plan_id="cycle",
    )
    result = await engine.start(ExecutionRequest(plan=cyclic, task_id="t4", correlation_id="c4"))
    assert result.outcome == TerminalOutcome.BLOCKED
    assert "cycle" in (result.summary or "")


@pytest.mark.asyncio
async def test_run_state_transitions_are_enforced() -> None:
    """§7: illegal run transitions raise immediately."""
    import pytest

    from atlas.tooling.execution.models import IllegalExecutionTransition, RunStatus, transition_run

    with pytest.raises(IllegalExecutionTransition):
        transition_run(RunStatus.COMPLETED, RunStatus.RUNNING)
    assert transition_run(RunStatus.RETRYING, RunStatus.RUNNING).value == "RUNNING"
