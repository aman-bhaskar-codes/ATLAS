#!/usr/bin/env python3
"""Hot-path benchmark report — p50/p95/p99 latency for pure orchestration stages.

Usage: uv run python benchmarks/run.py
Writes benchmarks/report.json (append) so trends are comparable over time.

Phase 39: eight deterministic CPU stages of the cognitive runtime are measured
here; Prompt 3 adds five knowledge-fabric stages (BM25 build/query, query
routing, feature reranking, injection scan) so the fabric's hot-path CPU cost
is tracked the same way. Stages that involve I/O (retrieval, providers, DB)
are covered by the gated live suite — this script covers only the paths that
must stay fast regardless of network, so a regression shows up as a code cost,
not a flaky network number. The model-selection and grounding stages
deliberately exercise the Phase 4/5 selector and the Phase 12 verifier so
their CPU cost is tracked.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from atlas.infra.cognition import (
    Complexity,
    Evidence,
    GoalState,
    RiskLevel,
    TaskDomain,
    TaskIntent,
)
from atlas.infra.ids import CorrelationId
from atlas.intelligence.capabilities import Capability
from atlas.intelligence.contracts import Constraints, ModelSpec
from atlas.intelligence.health.health_monitor import HealthMonitor
from atlas.intelligence.registry.capability_index import CapabilityIndex
from atlas.intelligence.registry.model_registry import ModelRegistry
from atlas.intelligence.selection.selector import ModelSelector
from atlas.knowledge.bm25 import BM25Index
from atlas.knowledge.domain import FabricChunk, KnowledgeDocument, SourceType
from atlas.knowledge.injection import scan_for_injection
from atlas.knowledge.reranking import FeatureReranker
from atlas.knowledge.retrieval import Candidate
from atlas.knowledge.router import QueryRouter
from atlas.orchestration.context_engine import ContextCompactor
from atlas.orchestration.plan_parsing import plan_from_llm_json
from atlas.orchestration.registry import ToolMetadata, ToolRegistry
from atlas.orchestration.tool_routing import ToolHealthTracker, ToolRouter
from atlas.orchestration.types import Observation, Thought
from atlas.orchestration.understanding import capabilities_from_intent, select_reasoning_level
from atlas.orchestration.verification import GroundingVerifier

N = 300


def _percentiles(fn: Callable[[], object], repeat: int = N) -> dict[str, float]:
    samples = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000)
    samples.sort()
    return {
        "p50_ms": round(statistics.median(samples), 4),
        # -1: percentile index into a 0-based sorted list.
        "p95_ms": round(samples[int(len(samples) * 0.95) - 1], 4),
        "p99_ms": round(samples[int(len(samples) * 0.99) - 1], 4),
    }


def _bench_model_registry() -> ModelRegistry:
    """Five reasoning-capable models so the selector has a real ranking job."""
    specs = {}
    for i in range(5):
        spec = ModelSpec(
            id=f"m{i}",
            provider=f"p{i}",
            provider_model=f"pm{i}",
            context_length=8000 + i * 1000,
            usd_per_1m_input=float(i),
            usd_per_1m_output=float(i),
            latency_estimate_ms=100 + i * 200,
            capabilities=frozenset([Capability.REASONING]),
            supports_reasoning=(i % 2 == 0),
            quality_score=0.5 + i * 0.08,
        )
        specs[spec.id] = spec
    return ModelRegistry(specs)


def main() -> int:
    # ── Stage 1: plan parsing (20-step DAG) ───────────────────────────
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

    # ── Stage 2: context compaction (60 turns) ────────────────────────
    compactor = ContextCompactor()
    history = [
        (Thought(step=i, content=f"t{i} " * 20), Observation(step=i, ok=True, content="o " * 40)) for i in range(60)
    ]

    # ── Stage 3: tool routing (10 tools) ──────────────────────────────
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

    # ── Stages 4/5/7: intent-derived pure logic ───────────────────────
    intent = TaskIntent(
        objective="Delete the temp cache directory and confirm it is gone",
        domain=TaskDomain.FILESYSTEM,
        success_criteria=("directory no longer exists", "no error on stat"),
        risk=RiskLevel.HIGH,
        complexity=Complexity.MODERATE,
        likely_side_effects=("removes files from disk",),
    )

    # ── Stage 6: model selection ranking (Phase 4/5) ──────────────────
    selector = ModelSelector(CapabilityIndex(_bench_model_registry()), HealthMonitor())
    required = frozenset([Capability.REASONING])
    constraints = Constraints()  # FAST tier default; DEEP weighting covered in the unit suite

    # ── Stage 8: grounding verification (Phase 12) ────────────────────
    grounder = GroundingVerifier()
    goal = GoalState.from_intent(intent)
    evidence = tuple(
        Evidence(source=f"filesystem:{i}", operation="read", ok=True, summary=f"read ok {i}") for i in range(4)
    )
    loop = asyncio.new_event_loop()
    corr = CorrelationId("bench")

    # ── Knowledge fabric stages (CPU-only legs of the hot path) ───────
    bm25 = BM25Index()
    corpus = [
        (
            f"chunk_{i}",
            f"steam engine history section {i} boiler pressure valve efficiency "
            f"watt newcomen condenser thermodynamics coal horsepower",
        )
        for i in range(500)
    ]
    q_router = QueryRouter()
    rerank_doc = KnowledgeDocument(
        document_id="bench_doc",
        source_id="bench_doc",
        source_type=SourceType.WEB_PAGE,
        title="bench",
        uri="https://example.com/bench",
        retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
        authority=0.6,
        freshness=0.6,
    )
    candidates = [
        Candidate(
            chunk=FabricChunk(
                chunk_id=f"chk_{i}",
                document_id="bench_doc",
                content=f"steam engine boiler pressure efficiency passage {i}",
            ),
            document=rerank_doc,
            rrf_score=1.0 - i * 0.02,
        )
        for i in range(40)
    ]
    reranker = FeatureReranker()
    page = ("A benign article about steam engines. " * 60) + "Conclusion: efficiency improved."

    def _grounding() -> object:
        # Reuse one loop: verify() does no real awaiting, so this measures the
        # verifier's CPU cost, not event-loop construction.
        return loop.run_until_complete(
            grounder.verify(goal, "the directory was removed", corr, "", TaskDomain.FILESYSTEM, evidence)
        )

    stages: dict[str, dict[str, float]] = {
        "plan_parsing_20_steps": _percentiles(lambda: plan_from_llm_json(plan_data)),
        "context_compaction_60_turns": _percentiles(lambda: compactor.compact(history)),
        "tool_routing_10_tools": _percentiles(router.rank),
        "intent_capability_projection": _percentiles(lambda: capabilities_from_intent(intent)),
        "reasoning_level_selection": _percentiles(
            lambda: select_reasoning_level(intent.domain, intent.complexity, intent.risk)
        ),
        "model_selection_ranking_5_models": _percentiles(lambda: selector.select(required, constraints)),
        "goal_state_lifecycle": _percentiles(
            lambda: GoalState.from_intent(intent).with_progress(0.5).with_confidence(0.8)
        ),
        "grounding_verification": _percentiles(_grounding),
        "bm25_build_500_chunks": _percentiles(lambda: bm25.build(corpus)),
        "bm25_query_500_chunks": _percentiles(lambda: bm25.query("newcomen boiler pressure efficiency", k=20)),
        "query_routing_multi_hop": _percentiles(
            lambda: q_router.route("How does memory retrieval work and how does it compare to vector search?")
        ),
        "feature_reranking_40_candidates": _percentiles(
            lambda: reranker.rerank("steam engine history", candidates, k=20)
        ),
        "injection_scan_2kb_page": _percentiles(lambda: scan_for_injection(page)),
    }
    loop.close()

    report = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "stages": stages}
    out = Path(__file__).resolve().parent / "report.json"
    entries = []
    if out.exists():
        try:
            entries = json.loads(out.read_text())
        except json.JSONDecodeError:
            entries = []
    entries.append(report)
    out.write_text(json.dumps(entries, indent=2))

    print(f"benchmark run {report['ts']} (n={N} per stage, {len(stages)} stages)")
    for stage, stats in stages.items():
        print(f"  {stage:36s} p50={stats['p50_ms']:.4f}ms  p95={stats['p95_ms']:.4f}ms  p99={stats['p99_ms']:.4f}ms")
    print(f"appended to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
