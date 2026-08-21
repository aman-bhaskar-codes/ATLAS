"""Shared fixtures for Knowledge Fabric tests.

Harness = lexical-only fabric: FabricStore on the test DB, HybridRetriever
with BM25 but no embedder/vector, canonical IngestionPipeline, and a
KnowledgeFabric with extractive synthesis (model=None). Deterministic, free,
and exercises the full SOURCE→…→CITE path.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio

from atlas.infra.db import Database
from atlas.knowledge.bm25 import BM25Index
from atlas.knowledge.cache import QueryResultCache
from atlas.knowledge.citations import CitationEngine
from atlas.knowledge.engine import KnowledgeFabric
from atlas.knowledge.evidence import ClaimExtractor, ClaimVerifier, ContradictionDetector, EvidenceSelector
from atlas.knowledge.ingestion import IngestionPipeline
from atlas.knowledge.reranking import FeatureReranker
from atlas.knowledge.retrieval import HybridRetriever
from atlas.knowledge.router import QueryRouter
from atlas.knowledge.store import FabricStore
from atlas.knowledge.synthesis import AnswerSynthesizer
from atlas.knowledge.telemetry import RagTelemetry
from tests.fakes import FakeClock, FakeIdGen

NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


@pytest.fixture
def ids() -> FakeIdGen:
    return FakeIdGen()


@pytest_asyncio.fixture
async def store(memory_db: Database) -> FabricStore:
    return FabricStore(memory_db)


@pytest_asyncio.fixture
async def retriever(store: FabricStore) -> AsyncIterator[HybridRetriever]:
    r = HybridRetriever(store, BM25Index(), None, None)
    await r.rebuild()
    yield r


@pytest_asyncio.fixture
async def pipeline(
    store: FabricStore, retriever: HybridRetriever, ids: FakeIdGen, clock: FakeClock
) -> IngestionPipeline:
    return IngestionPipeline(store, retriever, ids, clock)


@pytest_asyncio.fixture
async def fabric(
    memory_db: Database,
    store: FabricStore,
    retriever: HybridRetriever,
    ids: FakeIdGen,
    clock: FakeClock,
) -> tuple[KnowledgeFabric, RagTelemetry]:
    telemetry = RagTelemetry()
    telemetry.attach_db(memory_db)
    f = KnowledgeFabric(
        retriever=retriever,
        reranker=FeatureReranker(),
        selector=EvidenceSelector(ids, clock),
        contradictions=ContradictionDetector(),
        claims=ClaimExtractor(),
        verifier=ClaimVerifier(),
        synthesizer=AnswerSynthesizer(CitationEngine(), model=None),
        router=QueryRouter(),
        telemetry=telemetry,
        ids=ids,
        clock=clock,
        cache=QueryResultCache(),
    )
    return f, telemetry


# ── embedding/vector fakes for the dense leg ───────────────────────────
class FakeEmbedder:
    """Embedder that fails on demand or returns a constant vector."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    async def embed(self, text: str) -> list[float]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("embedding unavailable")
        return [0.1, 0.2, 0.3]


class FakeVectorHit:
    def __init__(self, ref: str, score: float = 0.9, text: str = "") -> None:
        self.ref = ref
        self.score = score
        self.text = text


class FakeVector:
    """Vector store double: returns configurable hits for search_knowledge."""

    def __init__(self, hits: list[FakeVectorHit] | None = None, *, fail: bool = False) -> None:
        self.hits = hits or []
        self.fail = fail

    async def search_knowledge(self, query_embedding: list[float], k: int) -> list[Any]:
        if self.fail:
            raise RuntimeError("vector store unavailable")
        return self.hits[:k]

    async def add_knowledge_chunk(
        self, chunk_id: str, text: str, embedding: list[float], metadata: dict[str, Any]
    ) -> str:
        if self.fail:
            raise RuntimeError("vector store unavailable")
        return f"kc_{chunk_id}"


class FakeSynthModel:
    """Synthesizer model double with a scripted completion."""

    def __init__(self, text: str) -> None:
        self._text = text
        self.calls: list[tuple[str, str]] = []

    async def complete(self, system: str, prompt: str) -> str:
        self.calls.append((system, prompt))
        return self._text


SAMPLE_DOC = """# ATLAS Fabric Notes

The knowledge fabric normalizes every source into one canonical document.
Chunking is structure-aware: headings, paragraphs, and tables guide the
boundaries so logical sections are never shredded.

## Retrieval

Hybrid retrieval fuses a lexical BM25 leg with a dense vector leg using
reciprocal rank fusion. The feature reranker then applies authority and
freshness signals before evidence selection.

## Evidence

Answers are built strictly from evidence. Citations are constructed from
evidence records, never invented by the model.
"""

SAMPLE_DOC_B = """# Cooking With Steam

Steaming vegetables preserves water-soluble vitamins better than boiling.
A bamboo steamer sits over simmering water and cooks gently in about five
minutes. Season after cooking, not before.
"""
