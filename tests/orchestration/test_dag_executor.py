"""DAG executor tests — parallel batches, dependency blocking, safety path."""

from __future__ import annotations

import asyncio
from typing import Any

from atlas.infra.ids import CorrelationId
from atlas.orchestration.dag_executor import DagExecutor
from atlas.orchestration.types import Action, Observation, Plan, PlanStep

CORR = CorrelationId("corr-1")


class RecordingDispatcher:
    """Fake dispatcher that records actions and simulates latency/failures."""

    def __init__(self, failures: set[int] | None = None, delay_s: float = 0.05) -> None:
        self.calls: list[tuple[str, str]] = []
        self.failures = failures or set()
        self._delay = delay_s

    async def dispatch(self, action: Action, correlation_id: CorrelationId) -> Observation:
        assert action.tool is not None and action.operation is not None
        self.calls.append((action.tool, action.operation))
        await asyncio.sleep(self._delay)
        ok = action.step not in self.failures
        return Observation(
            step=action.step,
            ok=ok,
            content=f"done {action.step}" if ok else None,
            error=None if ok else f"boom {action.step}",
        )


def _plan(steps: list[dict[str, Any]]) -> Plan:
    return Plan(
        goal="g",
        steps=tuple(PlanStep(**s) for s in steps),
    )


class TestDagExecutor:
    async def test_independent_steps_run_and_all_succeed(self) -> None:
        plan = _plan(
            [
                {"index": 0, "intent": "a", "tool": "fs", "operation": "read"},
                {"index": 1, "intent": "b", "tool": "fs", "operation": "read"},
                {"index": 2, "intent": "c", "tool": "fs", "operation": "read"},
            ]
        )
        d = RecordingDispatcher()
        results = await DagExecutor(d).execute(plan, CORR)
        assert set(results) == {0, 1, 2}
        assert all(o.ok for o in results.values())
        assert len(d.calls) == 3

    async def test_parallel_execution_is_faster_than_serial(self) -> None:
        plan = _plan([{"index": i, "intent": "s", "tool": "fs", "operation": "read"} for i in range(4)])
        d = RecordingDispatcher(delay_s=0.1)
        exec = DagExecutor(d, max_concurrency=4)
        import time

        t0 = time.perf_counter()
        await exec.execute(plan, CORR)
        elapsed = time.perf_counter() - t0
        # Serial would be 4 * 0.1 = 400ms; parallel must be well under.
        assert elapsed < 0.3, f"DAG did not parallelize: {elapsed:.3f}s"

    async def test_failure_blocks_dependents(self) -> None:
        plan = _plan(
            [
                {"index": 0, "intent": "root", "tool": "fs", "operation": "read"},
                {"index": 1, "intent": "child", "tool": "fs", "operation": "read", "depends_on": [0]},
                {"index": 2, "intent": "independent", "tool": "fs", "operation": "read"},
            ]
        )
        d = RecordingDispatcher(failures={0})
        results = await DagExecutor(d).execute(plan, CORR)
        assert results[0].ok is False
        assert results[2].ok is True
        assert 1 not in results  # blocked by failed dependency
        assert len(d.calls) == 2

    async def test_dependency_order_respected(self) -> None:
        plan = _plan(
            [
                {"index": 0, "intent": "first", "tool": "fs", "operation": "read"},
                {"index": 1, "intent": "second", "tool": "fs", "operation": "write", "depends_on": [0]},
            ]
        )
        d = RecordingDispatcher(delay_s=0.01)
        await DagExecutor(d).execute(plan, CORR)
        # The read must be dispatched before the write.
        assert d.calls[0] == ("fs", "read")
        assert d.calls[1] == ("fs", "write")

    async def test_steps_without_tool_are_not_executable(self) -> None:
        plan = _plan(
            [
                {"index": 0, "intent": "think about it"},  # no tool/operation
            ]
        )
        d = RecordingDispatcher()
        exec = DagExecutor(d)
        assert exec.executable_steps(plan) == ()
        results = await exec.execute(plan, CORR)
        assert results == {}
        assert d.calls == []

    async def test_concurrency_is_bounded(self) -> None:
        plan = _plan([{"index": i, "intent": "s", "tool": "fs", "operation": "read"} for i in range(6)])
        peak = 0
        current = 0
        lock = asyncio.Lock()

        class _ConcDispatcher:
            async def dispatch(self, action: Action, cid: CorrelationId) -> Observation:
                nonlocal peak, current
                async with lock:
                    current += 1
                    peak = max(peak, current)
                await asyncio.sleep(0.03)
                async with lock:
                    current -= 1
                return Observation(step=action.step, ok=True, content="ok")

        await DagExecutor(_ConcDispatcher(), max_concurrency=2).execute(plan, CORR)  # type: ignore[arg-type]
        assert peak <= 2, f"concurrency cap violated: peak={peak}"

    async def test_actions_go_through_dispatcher_only(self) -> None:
        """The DAG executor must never execute tools any other way."""
        plan = _plan([{"index": 0, "intent": "a", "tool": "fs", "operation": "read"}])
        d = RecordingDispatcher()
        await DagExecutor(d).execute(plan, CORR)
        # One dispatch per step — no hidden direct execution.
        assert len(d.calls) == 1
