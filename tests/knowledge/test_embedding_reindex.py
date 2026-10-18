"""Embedding migrations preserve SQL evidence and lexical retrieval."""

from __future__ import annotations

from typing import Any

from atlas.knowledge.domain import SourceType
from atlas.knowledge.ingestion import IngestionPipeline
from atlas.knowledge.retrieval import HybridRetriever
from atlas.knowledge.store import FabricStore
from tests.fakes import FakeClock, FakeIdGen
from tests.knowledge.harness import FakeEmbedder, FakeVector


class NamespacedEmbedder(FakeEmbedder):
    embedding_space = "ollama|http://127.0.0.1:11434|bge-m3"


class NamespacedVector(FakeVector):
    def __init__(self, *, fail: bool = False) -> None:
        super().__init__(fail=fail)
        self.records: dict[str, tuple[str, list[float], dict[str, Any]]] = {}

    async def add_knowledge_chunk(
        self, chunk_id: str, text: str, embedding: list[float], metadata: dict[str, Any]
    ) -> str:
        ref = await super().add_knowledge_chunk(chunk_id, text, embedding, metadata)
        self.records[chunk_id] = (text, embedding, metadata)
        return ref


async def test_reingestion_refreshes_namespace_without_duplicating_document(
    pipeline: IngestionPipeline, retriever: HybridRetriever, store: FabricStore, ids: FakeIdGen, clock: FakeClock
) -> None:
    text = "Orchid turbine produces 275 megawatts."
    original = await pipeline.ingest(source_id="report", source_type=SourceType.LOCAL_FILE, content=text)
    vectors = NamespacedVector()
    upgraded = IngestionPipeline(store, retriever, ids, clock, embedder=NamespacedEmbedder(), vector=vectors)
    job = await upgraded.ingest(source_id="report", source_type=SourceType.LOCAL_FILE, content=text)
    assert job.document_id == original.document_id
    doc = await store.get_document(job.document_id or "")
    assert doc is not None and doc.metadata["embedding_space"] == "ollama|http://127.0.0.1:11434|bge-m3"
    assert len(await store.list_documents()) == 1
    assert vectors.records


async def test_non_destructive_reindex_rebuilds_new_collection_from_sql(
    pipeline: IngestionPipeline, retriever: HybridRetriever, store: FabricStore, ids: FakeIdGen, clock: FakeClock
) -> None:
    job = await pipeline.ingest(
        source_id="report.pdf",
        source_type=SourceType.LOCAL_FILE,
        content="Orchid turbine produces 275 megawatts.",
        provenance={"pages": [{"page": 1, "start": 0, "end": 36, "method": "tesseract"}]},
    )
    before = await store.all_chunks()
    vectors = NamespacedVector()
    upgraded = IngestionPipeline(store, retriever, ids, clock, embedder=NamespacedEmbedder(), vector=vectors)
    result = await upgraded.reindex_embeddings(limit_documents=1)
    assert result["documents"] == 1 and result["embedded"] == 1 and result["errors"] == 0
    after = await store.all_chunks()
    assert [c.chunk_id for c, _ in after] == [c.chunk_id for c, _ in before]
    assert after[0][1].document_id == job.document_id
    assert after[0][1].provenance == before[0][1].provenance
    assert after[0][1].metadata["embedding_space"] == "ollama|http://127.0.0.1:11434|bge-m3"
    assert vectors.records[after[0][0].chunk_id][2]["page_start"] == 1
    assert (await retriever.retrieve("Orchid turbine")).candidates
    again = await upgraded.reindex_embeddings(limit_documents=1)
    assert again["embedded"] == 0


async def test_failed_reindex_keeps_old_corpus_searchable_without_false_stamp(
    pipeline: IngestionPipeline, retriever: HybridRetriever, store: FabricStore, ids: FakeIdGen, clock: FakeClock
) -> None:
    job = await pipeline.ingest(source_id="report", source_type=SourceType.LOCAL_FILE, content="Orchid turbine output.")
    upgraded = IngestionPipeline(
        store, retriever, ids, clock, embedder=NamespacedEmbedder(), vector=NamespacedVector(fail=True)
    )
    result = await upgraded.reindex_embeddings(limit_documents=1)
    assert result["errors"] == 1 and result["embedded"] == 0
    assert (await retriever.retrieve("Orchid turbine")).candidates
    doc = await store.get_document(job.document_id or "")
    assert doc is not None and "embedding_space" not in doc.metadata


async def test_namespace_refresh_retains_existing_document_source_fields(
    pipeline: IngestionPipeline, retriever: HybridRetriever, store: FabricStore, ids: FakeIdGen, clock: FakeClock
) -> None:
    text = "Orchid turbine produces 275 megawatts."
    original = await pipeline.ingest(
        source_id="original.pdf",
        source_type=SourceType.LOCAL_FILE,
        uri="original.pdf",
        title="Original Report",
        content=text,
        metadata={"description": "retained note"},
        provenance={"pages": [{"page": 2, "start": 0, "end": len(text), "method": "tesseract"}]},
    )
    upgraded = IngestionPipeline(store, retriever, ids, clock, embedder=NamespacedEmbedder(), vector=NamespacedVector())
    await upgraded.ingest(source_id="copy.pdf", source_type=SourceType.LOCAL_FILE, content=text)
    doc = await store.get_document(original.document_id or "")
    assert doc is not None
    assert doc.title == "Original Report" and doc.uri == "original.pdf"
    assert doc.metadata["description"] == "retained note"
    assert doc.provenance["pages"][0]["page"] == 2
