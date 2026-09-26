"""Catalog performance benchmark (Part 2 §67/§84).

Synthetic catalog: 1,000 tools x 5 operations = 5,000 operations, synced
through the REAL sync engine into the REAL SQLite store. Measured (not
claimed) on this machine; bounds below are generous CI guards derived from
the first measurement, not aspirational targets.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from atlas.infra.db import Database
from atlas.tooling.models.identity import ToolNamespace
from tests.tooling.catalog_helpers import FakeSource, build_catalog, make_definition

N_TOOLS = 1_000
OPS_PER_TOOL = 5


def _synthetic_definitions() -> list[Any]:
    return [
        make_definition(
            f"synth_tool_{i:05d}",
            namespace=ToolNamespace.MCP,
            provider="bench",
            operations=tuple(f"operation_{j}" for j in range(OPS_PER_TOOL)),
            description=f"synthetic benchmark tool {i} for searching github repositories and files",
            network_required=True,
            tags=("benchmark", f"shard_{i % 10}"),
        )
        for i in range(N_TOOLS)
    ]


@pytest.mark.asyncio
async def test_catalog_performance_at_scale(memory_db: Database, capsys: Any) -> None:
    from atlas.tooling.models.tool_health import ToolRuntimeState, ToolStatus

    class BenchSource(FakeSource):
        """A source that reports runtime status, like the real registry-backed
        sources do — so its tools catalog as READY."""

        def runtime_status(self, tool_id: str) -> ToolStatus:
            return ToolStatus(state=ToolRuntimeState.READY, detail="benchmark")

    source = BenchSource("mcp:bench", _synthetic_definitions())
    catalog = build_catalog(memory_db, sources=[source])

    measured: dict[str, float] = {}

    t0 = time.perf_counter()
    await catalog.initialize()  # initial sync: 1,000 tools / 5,000 operations
    measured["initial_sync"] = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    await catalog.rebuild_index()  # full load from SQLite into the index
    measured["index_load"] = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    for i in range(0, N_TOOLS, 100):
        catalog.inspect(f"mcp:bench:synth_tool_{i:05d}")
    measured["exact_lookup_x100"] = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    for _ in range(20):
        catalog.find_candidates(operation="operation_3", limit=50)
    measured["structured_query_x20"] = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    for _ in range(20):
        catalog.search("search github repositories files", limit=20)
    measured["lexical_search_x20"] = (time.perf_counter() - t0) * 1000

    # Refresh/diff: identical second sync walks the full diff engine.
    t0 = time.perf_counter()
    await catalog.refresh_source("mcp:bench")
    measured["unchanged_refresh"] = (time.perf_counter() - t0) * 1000

    # Correctness at scale.
    assert len(catalog.find()) == N_TOOLS
    assert catalog.inspect("mcp:bench:synth_tool_00500") is not None
    assert len(catalog.find_candidates(operation="operation_3")) == N_TOOLS
    assert len(catalog.search("github repositories", limit=25)) > 0
    snapshot = catalog.snapshot()
    assert snapshot.tool_count == N_TOOLS
    assert snapshot.operation_count == N_TOOLS * OPS_PER_TOOL

    print("\n--- tool catalog benchmark (1,000 tools / 5,000 operations) ---")
    for name, ms in measured.items():
        print(f"{name:>24}: {ms:9.1f} ms")

    # Generous CI guards from measured baselines (10-50x headroom on the
    # dev-machine numbers) — these catch order-of-magnitude regressions,
    # they are NOT microbenchmarks.
    assert measured["index_load"] < 10_000, "index load regressed badly"
    assert measured["exact_lookup_x100"] < 500, "exact lookup regressed"
    assert measured["structured_query_x20"] < 2_000, "structured query regressed"
    assert measured["lexical_search_x20"] < 10_000, "lexical search regressed"
    assert measured["unchanged_refresh"] < 30_000, "refresh/diff regressed"
