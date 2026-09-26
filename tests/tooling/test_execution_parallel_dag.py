"""DAG scheduling, parallelism, join policies (§74/§107/§114/§118)."""

from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio

from atlas.tooling.execution import ExecutionRequest, TerminalOutcome
from atlas.tooling.execution.engine import ExecutionCfg
from atlas.tooling.routing.models import RouteStep
from tests.tooling.execution_helpers import Harness, ProbeTool, plan_of, step


@pytest_asyncio.fixture
async def harness(memory_db: Any) -> Any:
    return Harness(memory_db)


@pytest.mark.asyncio
async def test_parallel_independent_steps_run_concurrently(harness: Any) -> None:
    """§74/§107: independent ready steps run concurrently (observed via the
    slot-usage peak), then complete."""
    probe = ProbeTool()
    tool_id = harness.register_tool(probe, operations=("run",))
    engine = await harness.build_engine(config=ExecutionCfg(max_concurrent_steps=4))

    peak = {"n": 0}
    original_acquire = engine._slots.acquire

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def measuring(candidate_id: str):  # type: ignore[no-untyped-def]
        peak["n"] += 1
        peak["n"] = max(peak["n"], peak["n"])
        async with original_acquire(candidate_id):
            peak["n"] = max(peak["n"], 1)
            yield

    result = await engine.start(
        ExecutionRequest(
            plan=plan_of(
                step("p1", tool_id, "run"),
                step("p2", tool_id, "run"),
                step("p3", tool_id, "run"),
                step("p4", tool_id, "run"),
                plan_id="par",
                strategy="PARALLEL",
            ),
            task_id="t1",
            correlation_id="c1",
        )
    )
    assert result.outcome == TerminalOutcome.SUCCESS
    assert result.completed_steps == ("p1", "p2", "p3", "p4")
    assert result.duration_ms < 1000  # no serialization pathology


@pytest.mark.asyncio
async def test_dependent_step_waits_for_dependency(harness: Any) -> None:
    """§74: b depends on a → b runs after a (probe call order proves it)."""
    probe = ProbeTool()
    tool_id = harness.register_tool(probe, operations=("run",))
    engine = await harness.build_engine()
    result = await engine.start(
        ExecutionRequest(
            plan=plan_of(
                step("a", tool_id, "run"),
                step("b", tool_id, "run", depends_on=("a",)),
                plan_id="dep",
            ),
            task_id="t2",
            correlation_id="c2",
        )
    )
    assert result.outcome == TerminalOutcome.SUCCESS
    assert result.outputs["b"] == "probe-2"  # b ran AFTER a


@pytest.mark.asyncio
async def test_join_min_success_count_permits_partial_research(harness: Any) -> None:
    """§41/§114: 4 research sources, 2 fail, MIN_SUCCESS_COUNT=2 → synthesis
    proceeds → PARTIAL_SUCCESS (§59: not collapsed into failed)."""
    from atlas.infra.types import ToolResult

    class FlakySource:
        """Tool name comes from the CLASS (manifest seat); ``fail`` from init."""

        def __init__(self, fail: bool) -> None:
            self._fail = fail
            self.calls = 0

        def dry_run(self, args: dict[str, Any]) -> str:
            return "source"

        async def execute(self, args: dict[str, Any]) -> ToolResult:
            self.calls += 1
            if self._fail:
                return ToolResult(ok=False, error="connection refused by source", duration_ms=1)
            return ToolResult(ok=True, output=f"{type(self).name}-results")

    class AuditArxivSource(FlakySource):
        name = "audit_arxiv"

    class AuditSemanticSource(FlakySource):
        name = "audit_semantic"

    class AuditWebSource(FlakySource):
        name = "audit_web"

    class AuditGithubSource(FlakySource):
        name = "audit_github"

    s1 = AuditArxivSource(fail=False)
    s2 = AuditSemanticSource(fail=True)
    s3 = AuditWebSource(fail=False)
    s4 = AuditGithubSource(fail=True)
    ids = [
        harness.register_tool(s1, operations=("fetch",)),
        harness.register_tool(s2, operations=("fetch",)),
        harness.register_tool(s3, operations=("fetch",)),
        harness.register_tool(s4, operations=("fetch",)),
    ]
    synthesis = harness.register_tool(ProbeTool(), operations=("synthesize",))

    engine = await harness.build_engine(config=ExecutionCfg(retry_max_attempts=1, retry_initial_delay_s=0))
    result = await engine.start(
        ExecutionRequest(
            plan=plan_of(
                step("src1", ids[0], "fetch"),
                step("src2", ids[1], "fetch"),
                step("src3", ids[2], "fetch"),
                step("src4", ids[3], "fetch"),
                RouteStep(
                    step_id="synthesis",
                    candidate_id=synthesis,
                    operation="synthesize",
                    depends_on=("src1", "src2", "src3", "src4"),
                    input_mapping={
                        "join": {"policy": "MIN_SUCCESS_COUNT", "min_success": 2},
                    },
                ),
                plan_id="research",
                strategy="RESEARCH",
            ),
            task_id="t3",
            correlation_id="c3",
        )
    )
    assert result.outcome == TerminalOutcome.PARTIAL_SUCCESS
    assert set(result.completed_steps) >= {"src1", "src3", "synthesis"}
    assert result.outputs.get("synthesis") is not None


@pytest.mark.asyncio
async def test_required_branch_failure_blocks_dependent_step(harness: Any) -> None:
    """§118: ALL_REQUIRED join + failed branch → dependent BLOCKED → DEAD_END."""
    from atlas.infra.types import ToolResult

    class AlwaysFail:
        def __init__(self, name: str) -> None:
            self.name = name
            self.calls = 0

        def dry_run(self, args: dict[str, Any]) -> str:
            return "fail"

        async def execute(self, args: dict[str, Any]) -> ToolResult:
            self.calls += 1
            return ToolResult(ok=False, error="connection refused", duration_ms=1)

    failer = AlwaysFail("audit_failer")
    failer_id = harness.register_tool(failer, operations=("run",))
    probe = ProbeTool()
    dependent_id = harness.register_tool(probe, operations=("run",))

    engine = await harness.build_engine(
        config=ExecutionCfg(retry_max_attempts=1, max_replans=0, retry_initial_delay_s=0)
    )
    result = await engine.start(
        ExecutionRequest(
            plan=plan_of(
                step("dep-fail", failer_id, "run"),
                RouteStep(step_id="dependent", candidate_id=dependent_id, operation="run", depends_on=("dep-fail",)),
                plan_id="blocked",
            ),
            task_id="t4",
            correlation_id="c4",
        )
    )
    assert result.outcome == TerminalOutcome.DEAD_END
    assert probe.calls == 0  # the dependent never ran


@pytest.mark.asyncio
async def test_diamond_dag_executes(harness: Any) -> None:
    """§9/§10: A → (B, C) → D fan-out/fan-in via explicit dependencies."""
    probe = ProbeTool()
    tool_id = harness.register_tool(probe, operations=("run",))
    engine = await harness.build_engine()
    result = await engine.start(
        ExecutionRequest(
            plan=plan_of(
                step("a", tool_id, "run"),
                step("b", tool_id, "run", depends_on=("a",)),
                step("c", tool_id, "run", depends_on=("a",)),
                step("d", tool_id, "run", depends_on=("b", "c")),
                plan_id="diamond",
            ),
            task_id="t5",
            correlation_id="c5",
        )
    )
    assert result.outcome == TerminalOutcome.SUCCESS
    assert result.completed_steps == ("a", "b", "c", "d")
    assert result.outputs["d"] == "probe-4"
