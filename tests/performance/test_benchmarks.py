"""Performance smoke benchmarks.

Bounds are deliberately generous (10-50x observed) so CI variance never
flakes; they exist to catch ORDER-OF-MAGNITUDE regressions (accidental O(n^2),
sync I/O on the hot path). Precise p50/p95 numbers come from benchmarks/run.py.
"""

from __future__ import annotations

import time
from typing import Any

from atlas.orchestration.context_engine import ContextCompactor, ContextRanker, estimate_tokens
from atlas.orchestration.plan_parsing import plan_from_llm_json
from atlas.orchestration.registry import ToolMetadata, ToolRegistry
from atlas.orchestration.tool_routing import ToolHealthTracker, ToolRouter
from atlas.orchestration.types import Observation, Thought


def _elapsed(fn: Any, *args: Any, repeat: int = 1) -> float:
    start = time.perf_counter()
    for _ in range(repeat):
        fn(*args)
    return (time.perf_counter() - start) / repeat


def test_plan_parsing_fast() -> None:
    """Plan parsing must stay sub-millisecond (runs on every plan/replan)."""
    data = {
        "goal": "g",
        "steps": [
            {
                "index": i,
                "intent": f"step {i}",
                "tool": "filesystem",
                "operation": "read",
                "args": {"path": "/x"},
                "depends_on": [i - 1] if i else [],
            }
            for i in range(20)
        ],
        "risk": "medium",
    }
    elapsed = _elapsed(plan_from_llm_json, data, repeat=100)
    assert elapsed < 0.005, f"plan parsing regressed: {elapsed * 1000:.2f}ms/op"


def test_tool_routing_fast() -> None:
    """Router ranking is on the planning hot path — must be sub-millisecond."""
    reg = ToolRegistry()
    for i in range(10):
        reg.register(
            _FakeTool(f"tool{i}"),
            ("read", "write"),
            ToolMetadata(name=f"tool{i}", description="d", estimated_latency_ms=100 + i * 50, idempotent=i % 2 == 0),
        )
    router = ToolRouter(reg, ToolHealthTracker())
    elapsed = _elapsed(router.rank, repeat=100)
    assert elapsed < 0.005, f"tool routing regressed: {elapsed * 1000:.2f}ms/op"


def test_context_compaction_fast() -> None:
    """Compaction runs between steps — must stay well under a millisecond."""
    compactor = ContextCompactor()
    history = [
        (
            Thought(step=i, content=f"thought {i} " * 20),
            Observation(step=i, ok=i % 4 != 0, content="obs " * 40, error=None if i % 4 else "e"),
        )
        for i in range(60)
    ]
    elapsed = _elapsed(compactor.compact, history, repeat=50)
    assert elapsed < 0.005, f"compaction regressed: {elapsed * 1000:.2f}ms/op"
    # Behavior: bounded output.
    compacted = compactor.compact(history)
    assert len(compacted) <= compactor.budget.max_history_turns + 1
    assert compacted[0][0].content.startswith("[compact]")


def test_ranker_orders_failures_and_recency() -> None:
    obs_fail = Observation(step=0, ok=False, error="x")
    obs_ok = Observation(step=0, ok=True)
    assert ContextRanker.score(0, 10, obs_fail) > ContextRanker.score(0, 10, obs_ok)
    assert ContextRanker.score(9, 10, obs_ok) > ContextRanker.score(0, 10, obs_ok)


def test_token_estimate() -> None:
    assert estimate_tokens("abcd" * 10) == 10
    assert estimate_tokens("") == 1


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name

    def dry_run(self, args: dict[str, Any]) -> str:
        return "preview"

    async def execute(self, args: dict[str, Any]) -> Any:
        raise NotImplementedError
