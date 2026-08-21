"""End-to-end KnowledgeFabric tests (§0, §55, §98): the canonical query path."""

from __future__ import annotations

from atlas.knowledge.domain import FailureCause, RAGMode, SourceType
from atlas.knowledge.engine import KnowledgeFabric
from atlas.knowledge.ingestion import IngestionPipeline
from atlas.knowledge.telemetry import RagTelemetry
from tests.knowledge.harness import SAMPLE_DOC, SAMPLE_DOC_B


async def _seed(pipeline: IngestionPipeline) -> None:
    await pipeline.ingest(source_id="notes.md", source_type=SourceType.LOCAL_FILE, content=SAMPLE_DOC)
    await pipeline.ingest(source_id="cooking.md", source_type=SourceType.LOCAL_FILE, content=SAMPLE_DOC_B)


async def test_query_answers_from_evidence_with_citations(
    fabric: tuple[KnowledgeFabric, RagTelemetry], pipeline: IngestionPipeline
) -> None:
    f, telemetry = fabric
    await _seed(pipeline)
    answer = await f.query("How does hybrid retrieval fuse lexical and dense results?")
    assert answer.answered is True
    assert answer.text
    assert answer.evidence  # evidence-first
    assert answer.citations
    # citations are built FROM evidence, never invented
    ev_ids = {e.evidence_id for e in answer.evidence}
    assert all(c.evidence_id in ev_ids for c in answer.citations)
    assert [c.index for c in answer.citations] == list(range(1, len(answer.citations) + 1))
    # telemetry recorded the full path
    assert len(telemetry.records) == 1
    rec = telemetry.records[0]
    assert rec.answered is True
    assert rec.candidate_count > 0
    assert rec.evidence_count > 0
    assert rec.failure is None


async def test_query_refuses_honestly_and_records_retrieval_miss(
    fabric: tuple[KnowledgeFabric, RagTelemetry], pipeline: IngestionPipeline
) -> None:
    f, telemetry = fabric
    await _seed(pipeline)
    answer = await f.query("Tell me about zorbaxian quantum flurble dynamics")
    assert answer.answered is False
    assert answer.refusal_reason
    assert answer.citations == ()
    failures = telemetry.failures()
    assert failures and failures[0].failure is FailureCause.RETRIEVAL_MISS


async def test_repeated_query_hits_the_answer_cache(
    fabric: tuple[KnowledgeFabric, RagTelemetry], pipeline: IngestionPipeline
) -> None:
    f, telemetry = fabric
    await _seed(pipeline)
    first = await f.query("What does the feature reranker apply?")
    second = await f.query("what does the feature reranker apply?")  # case/whitespace normalized
    assert first.answered is True
    assert second.text == first.text
    assert len(telemetry.records) == 1  # cached: no second pipeline run
    assert f._cache.hits == 1  # white-box cache verification


async def test_refusals_are_never_served_from_cache(
    fabric: tuple[KnowledgeFabric, RagTelemetry], pipeline: IngestionPipeline
) -> None:
    f, telemetry = fabric
    await _seed(pipeline)
    first = await f.query("Tell me about zorbaxian quantum flurble dynamics")
    second = await f.query("Tell me about zorbaxian quantum flurble dynamics")
    assert first.answered is False and second.answered is False
    assert len(telemetry.records) == 2  # both ran; refusal not cached


async def test_invalidate_cache_forces_fresh_runs(
    fabric: tuple[KnowledgeFabric, RagTelemetry], pipeline: IngestionPipeline
) -> None:
    f, telemetry = fabric
    await _seed(pipeline)
    await f.query("How does hybrid retrieval fuse results?")
    f.invalidate_cache()
    await f.query("How does hybrid retrieval fuse results?")
    assert len(telemetry.records) == 2


async def test_mode_override_filters_source_types(
    fabric: tuple[KnowledgeFabric, RagTelemetry], pipeline: IngestionPipeline
) -> None:
    f, _ = fabric
    await _seed(pipeline)
    # BROWSER_RAG only looks at browser/web pages; corpus is local_file-only
    answer = await f.query("hybrid retrieval fusion", mode=RAGMode.BROWSER_RAG)
    assert answer.answered is False  # nothing in the allowed source types


async def test_memory_route_filters_to_private_sources(
    fabric: tuple[KnowledgeFabric, RagTelemetry], pipeline: IngestionPipeline
) -> None:
    f, _ = fabric
    await _seed(pipeline)
    # MEMORY_RAG permits local_file among private sources, so the answer still works
    answer = await f.query("what did I store about steamer cooking?", mode=RAGMode.MEMORY_RAG)
    assert answer.mode is RAGMode.MEMORY_RAG


async def test_telemetry_summary_aggregates(
    fabric: tuple[KnowledgeFabric, RagTelemetry], pipeline: IngestionPipeline
) -> None:
    f, telemetry = fabric
    await _seed(pipeline)
    await f.query("How does hybrid retrieval fuse lexical and dense results?")
    await f.query("Tell me about zorbaxian quantum flurble dynamics")
    summary = telemetry.summary()
    assert summary["count"] == 2
    assert summary["answered"] == 1
    assert summary["answer_rate"] == 0.5
    assert summary["failures"].get("RETRIEVAL_MISS") == 1
