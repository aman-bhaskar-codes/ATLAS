"""Routing latency benchmark (Part 3 §80): measured p50/p95/p99 per phase."""

from __future__ import annotations

import statistics
import time
from typing import Any

import pytest

from atlas.tooling.routing.config import RoutingCfg
from atlas.tooling.routing.domains import DomainRegistry, builtin_domains
from atlas.tooling.routing.judgment import DeterministicJudgmentProvider, JudgmentCascade
from atlas.tooling.routing.models import TaskIR
from atlas.tooling.routing.router import RoutingEngine
from atlas.tooling.routing.strategies import StrategyRegistry, builtin_strategies
from tests.tooling.catalog_helpers import build_catalog, make_definition, registry_with

ITERATIONS = 100


@pytest.fixture
async def engine(memory_db: Any) -> Any:
    from atlas.tooling.models.tool_health import ToolRuntimeState

    registry = registry_with(
        make_definition("filesystem", operations=("read", "list", "search", "write"), description="files"),
        make_definition("shell", operations=("read_only", "side_effect"), description="shell"),
        make_definition("knowledge", operations=("search", "research"), description="research", network_required=True),
    )
    for tid in ("native:atlas:filesystem", "native:atlas:shell", "native:atlas:knowledge"):
        registry.set_status(tid, ToolRuntimeState.READY)
    catalog = build_catalog(memory_db, registry)
    await catalog.initialize()

    domains = DomainRegistry()
    for definition in builtin_domains(knowledge_available=lambda: True, ide_available=lambda: False):
        domains.register(definition)
    strategies = StrategyRegistry()
    for definition in builtin_strategies():
        strategies.register(definition)
    return RoutingEngine(
        config=RoutingCfg(),
        domains=domains,
        strategies=strategies,
        catalog=catalog,
        cascade=JudgmentCascade(deterministic=DeterministicJudgmentProvider(rules={})),
        store=None,  # persistence excluded from the latency measurement
    )


@pytest.mark.asyncio
async def test_routing_latency_budgets_at_p50_p95_p99(engine: Any, capsys: Any) -> None:
    """§80: routing must be fast enough to sit before every task. Measured,
    not claimed; assertions are generous CI regression guards."""
    phase_samples: dict[str, list[float]] = {
        "full_route": [],
        "normalization": [],
        "candidate_discovery": [],
        "ranking_compilation": [],
    }

    for i in range(ITERATIONS):
        t = TaskIR(
            task_id=f"bench-{i}",
            correlation_id="bench",
            objective="Research latest papers on agent memory and compare approaches",
        )
        t0 = time.perf_counter()
        engine._normalizer = __import__("atlas.tooling.routing.normalize", fromlist=["TaskNormalizer"]).TaskNormalizer()
        t1 = time.perf_counter()
        phase_samples["normalization"].append((t1 - t0) * 1000)

        t0 = time.perf_counter()
        result = await engine.route(t)
        t1 = time.perf_counter()
        full_ms = (t1 - t0) * 1000
        phase_samples["full_route"].append(full_ms)

        t0 = time.perf_counter()
        engine._discovery.discover_all(t)
        t1 = time.perf_counter()
        phase_samples["candidate_discovery"].append((t1 - t0) * 1000)

        t0 = time.perf_counter()
        assert result.plan is not None
        engine._planner.build_graph(result.plan)
        t1 = time.perf_counter()
        phase_samples["ranking_compilation"].append((t1 - t0) * 1000)
        assert result.decision.decision_type == "route"

    print(f"\n--- routing latency over {ITERATIONS} fast-path routes ---")
    budgets = {"full_route": 50.0, "normalization": 5.0, "candidate_discovery": 20.0, "ranking_compilation": 10.0}
    for phase, samples in phase_samples.items():
        p50 = statistics.quantiles(samples, n=100)[49] if len(samples) > 1 else samples[0]
        p95 = statistics.quantiles(samples, n=100)[94] if len(samples) > 1 else samples[0]
        p99 = statistics.quantiles(samples, n=100)[98] if len(samples) > 1 else samples[0]
        print(f"{phase:>22}: p50={p50:7.3f}ms p95={p95:7.3f}ms p99={p99:7.3f}ms")
        # Jev/LLM rungs are OFF — the deterministic fast path must stay in the
        # sub-millisecond-to-low-ms band, far under its judgment budget.
        assert p99 < budgets[phase], f"{phase} p99 {p99:.3f}ms exceeded budget {budgets[phase]}ms"
