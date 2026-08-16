#!/usr/bin/env python3
"""Hot-path benchmark report — p50/p95 latency for pure orchestration stages.

Usage: uv run python benchmarks/run.py
Writes benchmarks/report.json (append) so trends are comparable over time.
Stages involving I/O (retrieval, providers, DB) are benchmarked by the gated
live suite; this script covers the deterministic CPU paths.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from atlas.orchestration.context_engine import ContextCompactor
from atlas.orchestration.plan_parsing import plan_from_llm_json
from atlas.orchestration.registry import ToolMetadata, ToolRegistry
from atlas.orchestration.tool_routing import ToolHealthTracker, ToolRouter
from atlas.orchestration.types import Observation, Thought

N = 200


def _percentiles(fn, repeat: int = N) -> dict[str, float]:
    samples = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000)
    samples.sort()
    return {
        "p50_ms": round(statistics.median(samples), 4),
        "p95_ms": round(samples[int(len(samples) * 0.95) - 1], 4),
    }


def main() -> int:
    plan_data = {
        "goal": "g",
        "steps": [
            {
                "index": i,
                "intent": f"s{i}",
                "tool": "filesystem",
                "operation": "read",
                "args": {"path": "/x"},
                "depends_on": [i - 1] if i else [],
            }
            for i in range(20)
        ],
    }
    compactor = ContextCompactor()
    history = [
        (Thought(step=i, content=f"t{i} " * 20), Observation(step=i, ok=True, content="o " * 40)) for i in range(60)
    ]
    reg = ToolRegistry()

    class _T:
        def __init__(self, n: str) -> None:
            self.name = n

        def dry_run(self, a: dict) -> str:
            return "p"

        async def execute(self, a: dict) -> None:
            raise NotImplementedError

    for i in range(10):
        reg.register(_T(f"tool{i}"), ("read", "write"), ToolMetadata(name=f"tool{i}"))
    router = ToolRouter(reg, ToolHealthTracker())

    report = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "stages": {
            "plan_parsing_20_steps": _percentiles(lambda: plan_from_llm_json(plan_data)),
            "context_compaction_60_turns": _percentiles(lambda: compactor.compact(history)),
            "tool_routing_10_tools": _percentiles(router.rank),
        },
    }
    out = Path(__file__).resolve().parent / "report.json"
    entries = []
    if out.exists():
        try:
            entries = json.loads(out.read_text())
        except json.JSONDecodeError:
            entries = []
    entries.append(report)
    out.write_text(json.dumps(entries, indent=2))

    print(f"benchmark run {report['ts']} (n={N} per stage)")
    for stage, stats in report["stages"].items():
        print(f"  {stage}: p50={stats['p50_ms']}ms p95={stats['p95_ms']}ms")
    print(f"appended to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
