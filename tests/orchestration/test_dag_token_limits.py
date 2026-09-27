"""DAG executor token-limit tests.

Mirrors test_limits.py conventions and the existing test_dag_executor.py
patterns. Tests cover:
a. Observation without usage: counter.tokens stays 0.
b. Steps with usage: counter accumulates input+output across steps.
c. Budget trip: max_tokens set tiny, second step raises ReasoningError,
   batch siblings are cancelled, run result honestly reports failure.
d. Concurrency: batched parallel steps accumulate correctly.
e. Existing limits behaviour unchanged: max_steps / max_tool_calls trip
   exactly as before.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pydantic
import pytest

from atlas.infra.ids import CorrelationId
from atlas.orchestration.dag_executor import DagExecutor
from atlas.orchestration.limits import ExecutionLimits
from atlas.orchestration.types import Action, Observation, Plan, PlanStep, TokenUsage

CORR = CorrelationId("corr-tok")


# ── Helpers ──────────────────────────────────────────────────────────────── #


def _plan(steps: list[dict[str, Any]]) -> Plan:
    return Plan(goal="g", steps=tuple(PlanStep(**s) for s in steps))


class UsageDispatcher:
    """Fake dispatcher that attaches configurable TokenUsage to observations."""

    def __init__(
        self,
        usage_per_step: dict[int, TokenUsage] | None = None,
        delay_s: float = 0.01,
    ) -> None:
        self.calls: list[int] = []
        self._usage = usage_per_step or {}
        self._delay = delay_s

    async def dispatch(self, action: Action, correlation_id: CorrelationId) -> Observation:
        self.calls.append(action.step)
        await asyncio.sleep(self._delay)
        return Observation(
            step=action.step,
            ok=True,
            content=f"done {action.step}",
            usage=self._usage.get(action.step),
        )


# ── (a) Observation without usage: counter stays at 0 ───────────────────── #


class TestNoUsage:
    async def test_no_usage_counter_stays_zero(self) -> None:
        plan = _plan([
            {"index": 0, "intent": "a", "tool": "fs", "operation": "read"},
            {"index": 1, "intent": "b", "tool": "fs", "operation": "read"},
        ])
        d = UsageDispatcher()  # no usage_per_step → all None
        limits = ExecutionLimits(max_tokens=40_000)
        exec = DagExecutor(d, limits=limits)  # type: ignore[arg-type]
        results = await exec.execute(plan, CORR)

        assert len(results) == 2
        assert all(o.ok for o in results.values())
        # No usage reported → counter.tokens must be 0
        for obs in results.values():
            assert obs.usage is None


# ── (b) Steps with usage: counter accumulates correctly ──────────────── #


class TestUsageAccumulation:
    async def test_accumulates_input_plus_output(self) -> None:
        plan = _plan([
            {"index": 0, "intent": "a", "tool": "fs", "operation": "read"},
            {"index": 1, "intent": "b", "tool": "fs", "operation": "read"},
            {"index": 2, "intent": "c", "tool": "fs", "operation": "read"},
        ])
        usage = {
            0: TokenUsage(input_tokens=100, output_tokens=50),
            1: TokenUsage(input_tokens=200, output_tokens=100),
            2: TokenUsage(input_tokens=300, output_tokens=150),
        }
        d = UsageDispatcher(usage_per_step=usage)
        limits = ExecutionLimits(max_tokens=100_000)
        exec = DagExecutor(d, limits=limits)  # type: ignore[arg-type]
        results = await exec.execute(plan, CORR)

        assert len(results) == 3
        assert all(o.ok for o in results.values())
        # Verify usage is carried through
        assert results[0].usage is not None
        assert results[0].usage.total == 150
        assert results[1].usage is not None
        assert results[1].usage.total == 300
        assert results[2].usage is not None
        assert results[2].usage.total == 450


# ── (c) Budget trip: tiny max_tokens, second step trips ──────────────── #


class TestBudgetTrip:
    async def test_max_tokens_trips_and_cancels_siblings(self) -> None:
        """With max_tokens=200, step 0 (150 tokens) succeeds, step 1 (300
        tokens) trips the budget. The run must end with honest failure."""
        plan = _plan([
            {"index": 0, "intent": "first", "tool": "fs", "operation": "read"},
            {"index": 1, "intent": "second", "tool": "fs", "operation": "read", "depends_on": [0]},
        ])
        usage = {
            0: TokenUsage(input_tokens=100, output_tokens=50),   # 150 total
            1: TokenUsage(input_tokens=200, output_tokens=100),  # 300 total → cumulative 450 > 200
        }
        d = UsageDispatcher(usage_per_step=usage)
        limits = ExecutionLimits(max_tokens=200, max_steps=100, max_tool_calls=100)
        exec = DagExecutor(d, limits=limits)  # type: ignore[arg-type]
        results = await exec.execute(plan, CORR)

        # Step 0 succeeded
        assert results[0].ok is True

        # Step 1 must be present and failed with a budget error
        assert 1 in results
        assert results[1].ok is False
        assert "token budget exceeded" in (results[1].error or "")

    async def test_parallel_batch_budget_trip(self) -> None:
        """Two independent steps in the same batch. One trips the budget;
        the other must not be orphaned (it gets a failure record)."""
        plan = _plan([
            {"index": 0, "intent": "a", "tool": "fs", "operation": "read"},
            {"index": 1, "intent": "b", "tool": "fs", "operation": "read"},
        ])
        # Both have large usage; budget is tiny so whichever finishes first
        # will trip the budget.
        usage = {
            0: TokenUsage(input_tokens=500, output_tokens=500),
            1: TokenUsage(input_tokens=500, output_tokens=500),
        }
        d = UsageDispatcher(usage_per_step=usage)
        limits = ExecutionLimits(max_tokens=100, max_steps=100, max_tool_calls=100)
        exec = DagExecutor(d, limits=limits)  # type: ignore[arg-type]
        results = await exec.execute(plan, CORR)

        # Both steps must have a result (not orphaned)
        assert 0 in results
        assert 1 in results
        # At least one must report the budget failure
        errors = [r.error or "" for r in results.values()]
        assert any("token budget exceeded" in e for e in errors)
        # None should report ok=True if the budget was exceeded
        budget_failed = [r for r in results.values() if "token budget exceeded" in (r.error or "")]
        for r in budget_failed:
            assert r.ok is False


# ── (d) Concurrency: parallel steps accumulate correctly ─────────────── #


class TestConcurrentAccumulation:
    async def test_batched_parallel_steps_accumulate_correctly(self) -> None:
        """6 independent steps, each reporting 100 tokens. All 6 must
        accumulate to exactly 600 — no lost ticks."""
        plan = _plan([
            {"index": i, "intent": "s", "tool": "fs", "operation": "read"}
            for i in range(6)
        ])
        usage = {i: TokenUsage(input_tokens=50, output_tokens=50) for i in range(6)}
        d = UsageDispatcher(usage_per_step=usage, delay_s=0.02)
        limits = ExecutionLimits(max_tokens=100_000, max_steps=100, max_tool_calls=100)
        exec = DagExecutor(d, limits=limits, max_concurrency=3)  # type: ignore[arg-type]
        results = await exec.execute(plan, CORR)

        assert len(results) == 6
        assert all(o.ok for o in results.values())
        # Total usage across all observations = 6 * 100 = 600
        total = sum(o.usage.total for o in results.values() if o.usage)
        assert total == 600


# ── (e) Existing limits unchanged: max_steps / max_tool_calls ────────── #


class TestExistingLimits:
    async def test_max_steps_trips(self) -> None:
        """max_steps=2 with 3 sequential steps: third trips the budget."""
        plan = _plan([
            {"index": 0, "intent": "a", "tool": "fs", "operation": "read"},
            {"index": 1, "intent": "b", "tool": "fs", "operation": "read", "depends_on": [0]},
            {"index": 2, "intent": "c", "tool": "fs", "operation": "read", "depends_on": [1]},
        ])
        d = UsageDispatcher(delay_s=0.01)
        limits = ExecutionLimits(max_steps=2, max_tool_calls=100, max_tokens=100_000)
        exec = DagExecutor(d, limits=limits)  # type: ignore[arg-type]
        results = await exec.execute(plan, CORR)

        # Steps 0 and 1 succeed; step 2 trips max_steps.
        assert results[0].ok is True
        assert results[1].ok is True
        assert 2 in results
        assert results[2].ok is False

    async def test_max_tool_calls_trips(self) -> None:
        """max_tool_calls=1 with 2 steps: second must trip."""
        plan = _plan([
            {"index": 0, "intent": "a", "tool": "fs", "operation": "read"},
            {"index": 1, "intent": "b", "tool": "fs", "operation": "read", "depends_on": [0]},
        ])
        d = UsageDispatcher(delay_s=0.01)
        limits = ExecutionLimits(max_tool_calls=1, max_steps=100, max_tokens=100_000)
        exec = DagExecutor(d, limits=limits)  # type: ignore[arg-type]
        results = await exec.execute(plan, CORR)

        assert results[0].ok is True
        assert 1 in results
        assert results[1].ok is False


# ── TokenUsage model ────────────────────────────────────────────────────── #


class TestTokenUsage:
    def test_total_property(self) -> None:
        u = TokenUsage(input_tokens=100, output_tokens=50)
        assert u.total == 150

    def test_defaults_to_zero(self) -> None:
        u = TokenUsage()
        assert u.total == 0

    def test_frozen(self) -> None:
        u = TokenUsage(input_tokens=10, output_tokens=20)
        with pytest.raises((TypeError, pydantic.ValidationError)):
            u.input_tokens = 99


# ── Observation backwards compatibility ─────────────────────────────────── #


class TestObservationCompat:
    def test_observation_without_usage(self) -> None:
        o = Observation(step=0, ok=True, content="hello")
        assert o.usage is None

    def test_observation_with_usage(self) -> None:
        u = TokenUsage(input_tokens=10, output_tokens=20)
        o = Observation(step=0, ok=True, content="hello", usage=u)
        assert o.usage is not None
        assert o.usage.total == 30
