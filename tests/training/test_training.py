"""Offline training tests (§67-74, §101-103): triplets, pipelines, adapter registry."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from atlas.infra.clock import Clock
from atlas.knowledge.domain import AdapterState
from atlas.knowledge.store import FabricStore
from atlas.training.pipelines import ModelAdapterRegistry, RerankerTrainingPipeline, RetrieverTrainingPipeline
from atlas.training.triplets import Triplet, TripletReport, mine_triplets

_STEAM = "Steam engines convert heat into mechanical work using pistons."
_BREAD = "Bread baking requires flour yeast and warm water for dough."


@dataclass
class ChunkLike:
    content: str


class FakeResolver:
    """ChunkResolver double: maps chunk_id → content or missing."""

    def __init__(self, chunks: dict[str, str]) -> None:
        self.chunks = chunks

    async def get_chunk(self, chunk_id: str) -> tuple[ChunkLike, None] | None:
        if chunk_id not in self.chunks:
            return None
        return ChunkLike(self.chunks[chunk_id]), None


# ── mine_triplets ───────────────────────────────────────────────────────
async def test_pos_neg_labels_produce_user_triplets() -> None:
    pairs = [
        {"query": "how do steam engines work", "chunk_id": "c1", "label": "correct"},
        {"query": "how do steam engines work", "chunk_id": "c2", "label": "incorrect"},
    ]
    report = await mine_triplets(pairs, FakeResolver({"c1": _STEAM, "c2": _BREAD}))
    assert len(report.triplets) == 1
    t = report.triplets[0]
    assert t.anchor == "how do steam engines work"
    assert t.positive == _STEAM
    assert t.negative == _BREAD
    assert t.hard is False  # user-labelled negative, not mined
    assert report.pairs_seen == 2
    assert report.hard_negatives_added == 0
    assert report.skipped_no_content == 0


async def test_positive_only_queries_mine_hard_negatives_from_pool() -> None:
    pairs = [
        {"query": "how do steam engines work", "chunk_id": "c1", "label": "correct"},
        {"query": "baking basics", "chunk_id": "c3", "label": "correct"},
    ]
    report = await mine_triplets(pairs, FakeResolver({"c1": _STEAM, "c3": _BREAD}))
    assert len(report.triplets) == 2
    assert all(t.hard for t in report.triplets)
    assert report.hard_negatives_added == 2
    by_anchor = {t.anchor: t.negative for t in report.triplets}
    assert by_anchor["how do steam engines work"] == _BREAD  # least overlap wins
    assert by_anchor["baking basics"] == _STEAM


async def test_negative_only_queries_are_skipped() -> None:
    pairs = [{"query": "q", "chunk_id": "c1", "label": "wrong_source"}]
    report = await mine_triplets(pairs, FakeResolver({"c1": _STEAM}))
    assert report.triplets == ()
    assert report.skipped_no_content == 1


async def test_unknown_labels_are_ignored() -> None:
    pairs = [{"query": "q", "chunk_id": "c1", "label": "great"}, {"query": "q", "chunk_id": "c1", "label": ""}]
    report = await mine_triplets(pairs, FakeResolver({"c1": _STEAM}))
    assert report.triplets == ()
    assert report.pairs_seen == 2
    assert report.skipped_no_content == 0


async def test_unresolvable_chunks_do_not_produce_triplets() -> None:
    pairs = [
        {"query": "q", "chunk_id": "missing", "label": "correct"},
        {"query": "q", "chunk_id": "c2", "label": "incorrect"},
    ]
    report = await mine_triplets(pairs, FakeResolver({"c2": _BREAD}))
    assert report.triplets == ()
    assert report.skipped_no_content == 1  # negative pair skipped without a positive


async def test_max_triplets_bounds_output() -> None:
    pairs = [
        {"query": "q", "chunk_id": "c1", "label": "correct"},
        {"query": "q", "chunk_id": "c2", "label": "correct"},
        {"query": "q", "chunk_id": "c3", "label": "incorrect"},
    ]
    resolver = FakeResolver({"c1": _STEAM, "c2": _BREAD, "c3": "Unrelated note about clouds and rain."})
    report = await mine_triplets(pairs, resolver, max_triplets=1)
    assert len(report.triplets) == 1


# ── ModelAdapterRegistry ────────────────────────────────────────────────
async def test_registry_lifecycle_experimental_to_active(store: FabricStore, clock: Clock) -> None:
    registry = ModelAdapterRegistry(store, clock)
    await registry.register("reranker", "feature_reranker_tuned", "v2", {"margin": 0.9})
    rows = await store.adapters("reranker")
    assert len(rows) == 1
    assert rows[0]["state"] == AdapterState.EXPERIMENTAL.value  # nothing is trusted by default
    assert await registry.active("reranker") is None

    await registry.validate("reranker", "feature_reranker_tuned", "v2", {"margin": 0.9})
    assert (await store.adapters("reranker"))[0]["state"] == AdapterState.VALIDATED.value

    await registry.activate("reranker", "feature_reranker_tuned", "v2", {"margin": 0.9})
    active = await registry.active("reranker")
    assert active is not None
    assert active["state"] == AdapterState.ACTIVE.value
    assert active["metrics"] == {"margin": 0.9}

    await registry.deprecate("reranker", "feature_reranker_tuned", "v2")
    assert await registry.active("reranker") is None


# ── offline pipelines ───────────────────────────────────────────────────
def _report() -> TripletReport:
    return TripletReport(
        triplets=(
            Triplet(
                anchor="steam engine history",
                positive="the steam engine history began with newcomen engines",
                negative="cakes and frosting recipes for birthday parties",
            ),
        ),
        pairs_seen=2,
        skipped_no_content=0,
        hard_negatives_added=0,
    )


async def test_reranker_pipeline_improves_or_keeps_margin(store: FabricStore, clock: Clock) -> None:
    registry = ModelAdapterRegistry(store, clock)
    result = await RerankerTrainingPipeline(registry).train(_report())
    assert result.kind == "reranker"
    assert result.metrics["margin"] >= result.metrics["baseline_margin"]
    assert result.metrics["triplets"] == 1.0
    assert set(result.payload) == {"relevance", "authority", "freshness", "overlap", "improved"}
    # trained weights enter as EXPERIMENTAL until the eval gate promotes them
    rows = await store.adapters("reranker")
    assert rows and rows[0]["state"] == AdapterState.EXPERIMENTAL.value


async def test_retriever_pipeline_exports_jsonl_dataset(store: FabricStore, clock: Clock, tmp_path: Path) -> None:
    registry = ModelAdapterRegistry(store, clock)
    report = _report()
    out = tmp_path / "datasets" / "triplets.jsonl"
    result = await RetrieverTrainingPipeline(registry).export_training_data(report, out)
    assert result.kind == "retriever"
    assert result.metrics["triplets"] == float(len(report.triplets))

    lines = Path(result.payload["path"]).read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == len(report.triplets)
    row = json.loads(lines[0])
    assert row["anchor"] == "steam engine history"
    assert row["hard"] is False

    rows = await store.adapters("retriever")
    assert rows and rows[0]["state"] == AdapterState.EXPERIMENTAL.value
