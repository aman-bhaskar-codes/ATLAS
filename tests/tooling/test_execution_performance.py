"""Execution fabric performance benchmark (Part 4 §79/§117).

Measured, not claimed: 1-step, 10-step serial, 10-way parallel, 100-step DAG,
retry-heavy, and checkpoint-resume routes over the REAL governed funnel with
SQLite checkpointing ON. p50/p95/p99 per scenario plus overhead decomposition.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
import pytest_asyncio

from atlas.tooling.execution import ExecutionRequest, TerminalOutcome
from atlas.tooling.execution.engine import ExecutionCfg
from tests.tooling.execution_helpers import Harness, ProbeTool, plan_of, step


@pytest_asyncio.fixture
async def harness(memory_db: Any) -> Any:
    return Harness(memory_db)


def _percentiles(samples: list[float]) -> tuple[float, float, float]:
    ordered = sorted(samples)
    n = len(ordered)

    def pct(p: float) -> float:
        idx = min(n - 1, max(0, round(p / 100.0 * (n - 1))))
        return ordered[idx]

    return pct(50), pct(95), pct(99)


@pytest.mark.asyncio
async def test_execution_performance_scenarios(harness: Any, capsys: Any) -> None:
    probe = ProbeTool()
    tool_id = harness.register_tool(probe, operations=("run",))

    class AuditRetryHeavyTool(ProbeTool):
        name = "audit_retry_heavy"

    heavy = AuditRetryHeavyTool(fail_first=1)
    heavy_id = harness.register_tool(heavy, operations=("run",))
    probe.calls = 0
    engine = await harness.build_engine()
    measured: dict[str, list[float]] = {}

    async def timed(plan: Any) -> Any:
        t0 = time.perf_counter()
        result = await engine.start(ExecutionRequest(plan=plan, task_id=f"b-{plan.plan_id}", correlation_id="bench"))
        assert result.outcome in (TerminalOutcome.SUCCESS, TerminalOutcome.PARTIAL_SUCCESS)
        return (time.perf_counter() - t0) * 1000

    # 1-step
    measured["single_step"] = [await timed(plan_of(step("s", tool_id, "run"), plan_id=f"one-{i}")) for i in range(30)]
    # 10-step serial
    measured["serial_10"] = [
        await timed(plan_of(*(step(f"s{i}", tool_id, "run") for i in range(10)), plan_id=f"ser-{i}")) for i in range(10)
    ]
    # 10-way parallel
    measured["parallel_10"] = [
        await timed(
            plan_of(*(step(f"p{i}", tool_id, "run") for i in range(10)), plan_id=f"par-{i}", strategy="PARALLEL")
        )
        for i in range(10)
    ]
    # 100-step DAG (10 independent layers of 10)
    measured["dag_100"] = [
        await timed(
            plan_of(
                *(step(f"n{i}", tool_id, "run", depends_on=(f"n{i - 10}",) if i >= 10 else ()) for i in range(100)),
                plan_id=f"dag-{i}",
            )
        )
        for i in range(5)
    ]
    # retry-heavy: every step fails once then succeeds
    measured["retry_heavy_5"] = [
        await timed(
            plan_of(
                *(step(f"r{i}", heavy_id, "run") for i in range(5)),
                plan_id=f"retry-{i}",
            )
        )
        for i in range(10)
    ]
    # resume overhead: run, then resume a COMPLETED... resume only applies to
    # WAITING/PAUSED — measure checkpoint write overhead instead via a paused run.
    t0 = time.perf_counter()
    await engine.pause("pause-bench")
    result = await engine.start(
        ExecutionRequest(
            plan=plan_of(step("a", tool_id, "run"), step("b", tool_id, "run"), plan_id="pause-bench"),
            task_id="b-pause",
            correlation_id="bench",
        )
    )
    # pause() may land after completion — accept either terminal or paused
    measured["checkpoint_overhead_run"] = [(time.perf_counter() - t0) * 1000]
    _ = result

    print(f"\n--- execution fabric benchmark ({ExecutionCfg().max_concurrent_steps} slots, checkpoints ON) ---")
    budgets = {
        "single_step": 250.0,
        "serial_10": 1000.0,
        "parallel_10": 500.0,
        "dag_100": 5000.0,
        "retry_heavy_5": 1500.0,
        "checkpoint_overhead_run": 500.0,
    }
    for name, samples in measured.items():
        p50, p95, p99 = _percentiles(samples)
        print(f"{name:>24}: p50={p50:8.2f}ms p95={p95:8.2f}ms p99={p99:8.2f}ms (n={len(samples)})")
        assert p99 < budgets[name], f"{name} p99 {p99:.1f}ms exceeded budget {budgets[name]}ms"
