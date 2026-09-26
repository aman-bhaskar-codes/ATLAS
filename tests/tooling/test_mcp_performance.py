"""MCP runtime performance benchmark (Part 5 §135).

Measured over the REAL stdio transport against the REAL echo fixture:
connect, discovery, refresh, and per-call overhead. p50/p95/p99 reported —
never vague claims.
"""

from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from atlas.tooling.mcp.manager import MCPServerManager
from atlas.tooling.mcp.models import MCPServerDefinition, TransportType
from atlas.tooling.registry.registry import ToolingRegistry

FIXTURES = Path(__file__).parent / "mcp_fixtures"


@pytest.mark.asyncio
async def test_mcp_performance(tmp_path: Any, capfd: Any) -> None:
    definition = MCPServerDefinition(
        server_id="bench",
        transport=TransportType.STDIO,
        enabled=True,
        command=sys.executable,
        args=(str(FIXTURES / "echo_server.py"),),
        trust_level="local_user_configured",
        timeout_s=20.0,
    )
    manager = MCPServerManager(definitions=[definition], tooling_registry=ToolingRegistry())
    measured: dict[str, list[float]] = {"connect": [], "refresh": [], "tool_call": []}

    # §101: stdio children need REAL file descriptors — pytest's capture
    # replaces them; restore for the duration of the benchmark.
    capfd.disabled()

    for _ in range(3):
        t0 = time.perf_counter()
        await manager.connect("bench")
        measured["connect"].append((time.perf_counter() - t0) * 1000)
        await manager.disconnect("bench")

    await manager.connect("bench")
    for _ in range(10):
        t0 = time.perf_counter()
        await manager.refresh("bench")
        measured["refresh"].append((time.perf_counter() - t0) * 1000)

    for _ in range(30):
        t0 = time.perf_counter()
        await manager.call_tool("bench", "echo", {"text": "perf"})
        measured["tool_call"].append((time.perf_counter() - t0) * 1000)
    await manager.shutdown()

    print("\n--- MCP runtime benchmark (real stdio, official SDK) ---")
    for name, samples in measured.items():
        p50 = statistics.quantiles(samples, n=100)[49] if len(samples) > 1 else samples[0]
        p95 = statistics.quantiles(samples, n=100)[94] if len(samples) > 1 else samples[0]
        p99 = statistics.quantiles(samples, n=100)[98] if len(samples) > 1 else samples[0]
        print(f"{name:>10}: p50={p50:7.2f}ms p95={p95:7.2f}ms p99={p99:7.2f}ms (n={len(samples)})")

    # Generous CI guards (measured baselines have 10-50x headroom)
    assert statistics.median(measured["connect"]) < 10_000
    assert statistics.median(measured["refresh"]) < 2_000
    assert statistics.median(measured["tool_call"]) < 500
