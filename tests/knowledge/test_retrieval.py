"""Hybrid retrieval tests (§15-17, §138): RRF fusion, degradation, filters."""

from __future__ import annotations

from pathlib import Path

from atlas.infra.db import Database
from atlas.knowledge.bm25 import BM25Index, tokenize
from atlas.knowledge.domain import SourceType
from atlas.knowledge.ingestion import IngestionPipeline
from atlas.knowledge.retrieval import HybridRetriever
from atlas.knowledge.store import FabricStore
from tests.knowledge.harness import SAMPLE_DOC, SAMPLE_DOC_B, FakeEmbedder, FakeVector, FakeVectorHit


def test_tokenize_lowercases_and_splits_words() -> None:
    assert tokenize("Hello, World! x_1") == ["hello", "world", "x_1"]


def test_bm25_ranks_relevant_document_first() -> None:
    idx = BM25Index()
    idx.build(
        [
            ("a", "steam engines converted heat into mechanical work"),
            ("b", "cakes require flour sugar and butter"),
            ("c", "the watt steam engine improved efficiency of engines"),
        ]
    )
    hits = idx.query("steam engine efficiency", k=3)
    assert hits[0][0] == "c"
    assert all(ref in {"a", "b", "c"} for ref, _ in hits)


def test_bm25_incremental_add_and_remove() -> None:
    idx = BM25Index()
    idx.add("a", "alpha beta gamma")
    idx.add("b", "delta epsilon")
    assert idx.size == 2
    assert idx.query("alpha")[0][0] == "a"
    idx.remove("a")
    assert idx.size == 1
    assert idx.query("alpha") == []
    idx.remove("missing")  # no-op
    assert idx.size == 1


def test_bm25_empty_corpus_and_empty_query() -> None:
    idx = BM25Index()
    assert idx.query("anything") == []
    idx.add("a", "text here")
    assert idx.query("   ") == []


async def test_lexical_only_retrieval_flags_honest_degradation(
    pipeline: IngestionPipeline, retriever: HybridRetriever
) -> None:
    await pipeline.ingest(source_id="a", source_type=SourceType.WEB_PAGE, content=SAMPLE_DOC)
    result = await retriever.retrieve("reciprocal rank fusion")
    assert result.candidates, "lexical leg must still return results"
    assert result.degraded is True
    assert "lexical only" in result.degradation_reason


async def test_retrieval_ranks_matching_document_first(
    pipeline: IngestionPipeline, retriever: HybridRetriever
) -> None:
    await pipeline.ingest(source_id="a", source_type=SourceType.WEB_PAGE, content=SAMPLE_DOC)
    await pipeline.ingest(source_id="b", source_type=SourceType.WEB_PAGE, content=SAMPLE_DOC_B)
    result = await retriever.retrieve("bamboo steamer vegetables vitamins", k=10)
    assert result.candidates
    top_doc = result.candidates[0].document
    assert top_doc.title == "Cooking With Steam"


async def test_source_type_filter_restricts_candidates(
    pipeline: IngestionPipeline, retriever: HybridRetriever
) -> None:
    await pipeline.ingest(source_id="a", source_type=SourceType.LOCAL_FILE, content=SAMPLE_DOC)
    await pipeline.ingest(source_id="b", source_type=SourceType.WEB_PAGE, content=SAMPLE_DOC_B)
    result = await retriever.retrieve("steamer", source_types=("local_file",))
    assert result.candidates == []  # the steamer doc is a web_page
    result2 = await retriever.retrieve("chunking structure headings", source_types=("local_file",))
    assert result2.candidates
    assert all(c.document.source_type is SourceType.LOCAL_FILE for c in result2.candidates)


async def test_dense_leg_participates_via_rrf_and_clears_degradation(
    pipeline: IngestionPipeline, store: FabricStore
) -> None:
    await pipeline.ingest(source_id="a", source_type=SourceType.WEB_PAGE, content=SAMPLE_DOC)
    corpus = await store.all_chunks()
    chunk_ids = [c.chunk_id for c, _ in corpus]
    # dense returns the LAST chunk first (reverse of lexical order for this query)
    hits = [FakeVectorHit(ref=f"kc_{cid}") for cid in reversed(chunk_ids)]
    hybrid = HybridRetriever(store, BM25Index(), FakeEmbedder(), FakeVector(hits))
    await hybrid.rebuild()
    result = await hybrid.retrieve("evidence citations", k=10)
    assert result.candidates
    assert result.degraded is False  # both legs contributed
    # the dense-top chunk should rank high thanks to RRF fusion
    top_ids = {c.chunk.chunk_id for c in result.candidates[:3]}
    assert chunk_ids[-1] in top_ids


async def test_dense_leg_failure_degrades_but_lexical_survives(
    pipeline: IngestionPipeline, store: FabricStore
) -> None:
    await pipeline.ingest(source_id="a", source_type=SourceType.WEB_PAGE, content=SAMPLE_DOC)
    hybrid = HybridRetriever(store, BM25Index(), FakeEmbedder(fail=True), FakeVector(fail=True))
    await hybrid.rebuild()
    result = await hybrid.retrieve("rank fusion")
    assert result.candidates  # lexical survives (§138)
    assert result.degraded is True
    assert "vector leg unavailable" in result.degradation_reason


async def test_drop_document_removes_chunks_from_index(
    pipeline: IngestionPipeline, retriever: HybridRetriever, store: FabricStore
) -> None:
    job = await pipeline.ingest(source_id="a", source_type=SourceType.WEB_PAGE, content=SAMPLE_DOC_B)
    assert (await retriever.retrieve("bamboo steamer")).candidates
    retriever.drop_document(job.document_id or "")
    assert (await retriever.retrieve("bamboo steamer")).candidates == []


async def test_retrieve_all_fuses_multiple_query_variants(
    pipeline: IngestionPipeline, retriever: HybridRetriever
) -> None:
    await pipeline.ingest(source_id="a", source_type=SourceType.WEB_PAGE, content=SAMPLE_DOC)
    await pipeline.ingest(source_id="b", source_type=SourceType.WEB_PAGE, content=SAMPLE_DOC_B)
    merged = await retriever.retrieve_all(["reciprocal rank fusion", "bamboo steamer vegetables"], k=10)
    docs = {c.document.title for c in merged.candidates}
    assert "ATLAS Fabric Notes" in docs and "Cooking With Steam" in docs
    # single-query fast path returns the same shape
    single = await retriever.retrieve_all(["bamboo steamer"], k=10)
    assert single.candidates


async def test_lazy_rebuild_recovers_from_unstarted_database() -> None:
    """Retriever constructed before DB use must degrade, not crash (§140)."""
    never_started = Database(Path("/tmp/atlas_never_started.db"))
    broken_store = FabricStore(never_started)
    hybrid = HybridRetriever(broken_store, BM25Index(), None, None)
    result = await hybrid.retrieve("anything at all")
    assert result.candidates == []
    assert result.degraded is True
    assert "corpus rebuild failed" in result.degradation_reason
