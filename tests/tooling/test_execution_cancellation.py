"""Cancellation + route staleness tests (§45/§75/§77/§118)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
import pytest_asyncio

from atlas.tooling.execution import ExecutionRequest, TerminalOutcome
from atlas.tooling.execution.engine import ExecutionCfg
from tests.tooling.execution_helpers import Harness, ProbeTool, SlowTool, fake_decision, plan_of, step


@pytest_asyncio.fixture
async def harness(memory_db: Any) -> Any:
    return Harness(memory_db)


@pytest.mark.asyncio
async def test_cancel_during_execution_terminates_cleanly(harness: Any) -> None:
    """§45/§75: cancellation during a step → CANCELLED, no new work started."""
    slow = SlowTool()
    tool_id = harness.register_tool(slow, operations=("run",))
    engine = await harness.build_engine(config=ExecutionCfg(default_step_timeout_s=8))

    async def cancel_soon() -> None:
        await asyncio.sleep(0.05)
        # find the run and cancel it
        runs = await engine.list_runs(limit=1)
        await engine.cancel(runs[0]["run_id"])

    task = asyncio.ensure_future(
        engine.start(
            ExecutionRequest(
                plan=plan_of(step("s1", tool_id, "run"), step("s2", tool_id, "run"), plan_id="cancel"),
                task_id="t1",
                correlation_id="c1",
            )
        )
    )
    _cancel_task = asyncio.ensure_future(cancel_soon())  # noqa: RUF006 — fires during the run
    result = await task
    assert result.outcome == TerminalOutcome.CANCELLED
    assert result.status.value == "CANCELLED"
    # §118: a cancelled task does not start NEW work — s2 never SUCCEEDED
    run = await engine.status(result.run_id)
    s2_state = run.step("s2")
    assert s2_state.status != __import__("atlas.tooling.execution.models", fromlist=["StepStatus"]).StepStatus.SUCCEEDED
    slow.release.set()


@pytest.mark.asyncio
async def test_cancelled_task_does_not_start_new_work(harness: Any) -> None:
    """§118 dedicated: once the token is cancelled at a boundary, the next
    ready step never starts."""
    probe = ProbeTool()
    tool_id = harness.register_tool(probe, operations=("run",))
    engine = await harness.build_engine(config=ExecutionCfg(retry_max_attempts=1, retry_initial_delay_s=0))

    run_id_holder: dict[str, str] = {}
    original_create = engine._create_run

    def create_and_cancel(request: Any) -> Any:
        run = original_create(request)
        run_id_holder["run_id"] = run.run_id
        engine._tokens[run.run_id].cancel()  # cancel BEFORE any step
        return run

    engine._create_run = create_and_cancel  # type: ignore[method-assign]
    result = await engine.start(
        ExecutionRequest(plan=plan_of(step("s1", tool_id, "run")), task_id="t2", correlation_id="c2")
    )
    assert result.outcome == TerminalOutcome.CANCELLED
    assert probe.calls == 0  # no work started


@pytest.mark.asyncio
async def test_route_staleness_dead_candidate_triggers_fallback(harness: Any) -> None:
    """§77/§30-§31: the catalog changed since routing; the primary candidate is
    gone → revalidation fails → the fallback candidate runs instead."""

    class FallbackProbe(ProbeTool):
        name = "audit_probe_fallback"

    primary = harness.register_tool(ProbeTool(), operations=("run",))
    probe = FallbackProbe()
    secondary = harness.register_tool(probe, operations=("run",))

    engine = await harness.build_engine(config=ExecutionCfg(retry_max_attempts=1, retry_initial_delay_s=0))
    engine.attach_decision("stale-plan", fake_decision(fallbacks=(secondary,)))

    # The world changed: the primary disappears from the registry, and the
    # catalog is re-synced — the route snapshot is now stale (§31).
    harness.registry.unregister(primary)
    catalog = engine._staleness._catalog
    await catalog.refresh_source("native:atlas")

    result = await engine.start(
        ExecutionRequest(
            plan=plan_of(step("s1", primary, "run"), plan_id="stale-plan"),
            task_id="t3",
            correlation_id="c3",
        )
    )
    assert result.outcome == TerminalOutcome.SUCCESS
    assert result.outputs["s1"] == "probe-1"  # the FALLBACK served the step
