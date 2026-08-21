"""Offline training pipelines + adapter registry (§70-74, §101-103).

Free-first policy: no gradient descent, no paid training endpoints. The
reranker is tuned by deterministic coordinate search over mined triplets;
the retriever pipeline EXPORTS triplet training data for the planned LoRA
stage (offline, when compute is available) and registers the dataset as an
EXPERIMENTAL adapter artifact.

Nothing produced here is trusted by default: adapters enter as EXPERIMENTAL
and only the evaluation layer's regression gate can promote them (§128-129).
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from atlas.infra.clock import Clock
from atlas.knowledge.domain import AdapterState
from atlas.knowledge.reranking import RerankWeights
from atlas.knowledge.store import FabricStore
from atlas.training.triplets import Triplet, TripletReport

_STOPWORDS = frozenset(
    "a an and are as at be but by for from has have how in is it its of on or that the this to was"
    " what when where which who will with".split()
)


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9_]+", text.lower()) if t not in _STOPWORDS and len(t) > 2]


def _uni_overlap(query: str, text: str) -> float:
    q = set(_tokens(query))
    return len(q & set(_tokens(text[:800]))) / max(len(q), 1)


def _bi_overlap(query: str, text: str) -> float:
    q_toks, t_toks = _tokens(query), _tokens(text[:800])
    q_bi = {(q_toks[i], q_toks[i + 1]) for i in range(len(q_toks) - 1)}
    t_bi = {(t_toks[i], t_toks[i + 1]) for i in range(len(t_toks) - 1)}
    return len(q_bi & t_bi) / max(len(q_bi), 1)


@dataclass(frozen=True)
class TrainingResult:
    kind: str  # "reranker" | "retriever"
    name: str
    version: str
    metrics: dict[str, float]
    payload: dict[str, Any]  # e.g. learned weights or dataset path


class RerankerTrainingPipeline:
    """Coordinate search over RerankWeights that maximizes triplet margin."""

    def __init__(self, registry: ModelAdapterRegistry) -> None:
        self._registry = registry

    async def train(self, report: TripletReport, *, baseline: RerankWeights | None = None) -> TrainingResult:
        triplets = list(report.triplets)
        base = baseline or RerankWeights()
        best_w, best_margin = base, _margin(base, triplets)

        grid = (0.0, 0.25, 0.5, 0.75, 1.0)
        for rel in grid:
            for ovl in grid:
                candidate = RerankWeights(
                    relevance=rel, overlap=ovl, authority=base.authority, freshness=base.freshness
                )
                m = _margin(candidate, triplets)
                if m > best_margin:
                    best_margin, best_w = m, candidate

        improved = best_margin > _margin(base, triplets)
        result = TrainingResult(
            kind="reranker",
            name="feature_reranker_tuned",
            version="v2" if improved else "v1",
            metrics={
                "margin": round(best_margin, 3),
                "baseline_margin": round(_margin(base, triplets), 3),
                "triplets": float(len(triplets)),
            },
            payload={
                "relevance": best_w.relevance,
                "authority": best_w.authority,
                "freshness": best_w.freshness,
                "overlap": best_w.overlap,
                "improved": improved,
            },
        )
        # EXPERIMENTAL until an evaluation experiment passes the gate (§128)
        await self._registry.register("reranker", result.name, result.version, result.metrics)
        return result


class RetrieverTrainingPipeline:
    """No local embedding fine-tune today: export triplets for the LoRA stage."""

    def __init__(self, registry: ModelAdapterRegistry) -> None:
        self._registry = registry

    async def export_training_data(self, report: TripletReport, path: Path) -> TrainingResult:
        rows = [
            {"anchor": t.anchor, "positive": t.positive[:4000], "negative": t.negative[:4000], "hard": t.hard}
            for t in report.triplets
        ]
        payload = "\n".join(json.dumps(r) for r in rows) + "\n"

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(payload, encoding="utf-8")

        await asyncio.to_thread(_write)
        result = TrainingResult(
            kind="retriever",
            name="retriever_triplet_dataset",
            version="v1",
            metrics={"triplets": float(len(rows)), "hard_negatives": float(report.hard_negatives_added)},
            payload={"path": str(path), "lora_plan": "contrastive InfoNCE on exported triplets, offline"},
        )
        await self._registry.register("retriever", result.name, result.version, result.metrics)
        return result


class ModelAdapterRegistry:
    """Lifecycle bookkeeping: EXPERIMENTAL → VALIDATED → ACTIVE → DEPRECATED."""

    def __init__(self, store: FabricStore, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    async def register(self, kind: str, name: str, version: str, metrics: dict[str, float]) -> None:
        await self._store.upsert_adapter(kind, name, version, AdapterState.EXPERIMENTAL, metrics, self._clock.now())

    async def validate(self, kind: str, name: str, version: str, metrics: dict[str, float]) -> None:
        await self._store.upsert_adapter(kind, name, version, AdapterState.VALIDATED, metrics, self._clock.now())

    async def activate(self, kind: str, name: str, version: str, metrics: dict[str, float]) -> None:
        await self._store.upsert_adapter(kind, name, version, AdapterState.ACTIVE, metrics, self._clock.now())

    async def deprecate(self, kind: str, name: str, version: str) -> None:
        await self._store.upsert_adapter(kind, name, version, AdapterState.DEPRECATED, {}, self._clock.now())

    async def active(self, kind: str) -> dict[str, Any] | None:
        for row in await self._store.adapters(kind):
            if row["state"] == AdapterState.ACTIVE.value:
                return row
        return None


def _margin(w: RerankWeights, triplets: list[Triplet]) -> float:
    if not triplets:
        return 0.0
    total = 0.0
    for t in triplets:
        s_pos = _score(w, t.anchor, t.positive)
        s_neg = _score(w, t.anchor, t.negative)
        total += max(0.0, min(1.0, 0.5 + (s_pos - s_neg)))
    return total / len(triplets)


def _score(w: RerankWeights, query: str, text: str) -> float:
    return (
        w.relevance * _bi_overlap(query, text)
        + w.overlap * _uni_overlap(query, text)
        + (w.authority + w.freshness) * 0.5
    )
