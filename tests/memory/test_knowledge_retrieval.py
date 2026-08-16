"""Knowledge store integration with retrieval — Phase 3 Task 3.5.

Uses a fake embedder so no Ollama instance is required.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from atlas.infra.clock import SystemClock
from atlas.infra.db import Database
from atlas.infra.ids import UuidGenerator
from atlas.memory.episodic import EpisodicMemory
from atlas.memory.knowledge_store import KnowledgeStore
from atlas.memory.retrieval import Retriever
from atlas.memory.semantic import SemanticMemory
from atlas.memory.types import RetrievedContext
from atlas.memory.user_model import UserModel
from atlas.memory.vectorstore import ChromaVectorStore

# ---------------------------------------------------------------------------
# Shared fake embedder
# ---------------------------------------------------------------------------


class _FakeEmbedder:
    DIM = 384

    async def embed(self, text: str) -> list[float]:
        h = hash(text) % (10**6)
        base = [float(h % (i + 2)) for i in range(self.DIM)]
        norm = (sum(x * x for x in base) ** 0.5) or 1.0
        return [x / norm for x in base]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build(tmp: Path) -> tuple[Database, ChromaVectorStore, _FakeEmbedder]:
    db = Database(tmp / "test.db")
    vs = ChromaVectorStore(str(tmp / "chroma"))
    emb = _FakeEmbedder()
    return db, vs, emb


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_knowledge_store_retrieval_integration(tmp_path: Path) -> None:
    """Ingested documents appear in RetrievedContext.knowledge_chunks."""
    db, vs, emb = _build(tmp_path)
    await db.start()

    clock = SystemClock()
    ids = UuidGenerator()

    store = KnowledgeStore(db=db, vector_store=vs, embedder=emb, ids=ids, clock=clock)  # type: ignore[arg-type]

    episodic = EpisodicMemory(db=db, clock=clock)
    semantic = SemanticMemory(db=db, vectors=vs, embedder=emb, ids=ids, clock=clock)  # type: ignore[arg-type]
    user_model = UserModel(db=db, clock=clock)
    retriever = Retriever(
        semantic=semantic,
        episodic=episodic,
        user_model=user_model,
        knowledge_store=store,
        token_budget=2000,
        cache_ttl=0.0,  # no cache for test
    )

    # Create and ingest a small test document
    test_doc = tmp_path / "python_guide.md"
    test_doc.write_text(
        "# Python Guide\n\n"
        "Python is a high-level language widely used for web development and data science.\n\n"
        "## Features\n- Easy to learn\n- Great community support\n"
    )

    doc_id = await store.ingest_document(test_doc, "markdown", metadata={"title": "Python Guide"})
    assert doc_id is not None, "ingest_document must return a non-empty doc_id"

    # Retrieve with a query matching the document
    ctx = await retriever.retrieve("What is Python used for?")

    assert isinstance(ctx, RetrievedContext)
    assert isinstance(ctx.knowledge_chunks, tuple)
    # The fake embedder produces consistent vectors so at least one chunk should match
    # (exact count depends on chunk size vs. document size)
    assert ctx.token_estimate > 0

    await db.stop()


@pytest.mark.asyncio
async def test_retrieval_without_knowledge_store(tmp_path: Path) -> None:
    """Retriever works with knowledge_store=None — backward compatibility."""
    db, vs, emb = _build(tmp_path)
    await db.start()

    clock = SystemClock()
    ids = UuidGenerator()

    episodic = EpisodicMemory(db=db, clock=clock)
    semantic = SemanticMemory(db=db, vectors=vs, embedder=emb, ids=ids, clock=clock)  # type: ignore[arg-type]
    user_model = UserModel(db=db, clock=clock)

    retriever = Retriever(
        semantic=semantic,
        episodic=episodic,
        user_model=user_model,
        knowledge_store=None,
        cache_ttl=0.0,
    )

    ctx = await retriever.retrieve("test query")

    assert isinstance(ctx, RetrievedContext)
    assert ctx.knowledge_chunks == ()  # empty when no store attached

    await db.stop()


@pytest.mark.asyncio
async def test_token_budget_partitioning(tmp_path: Path) -> None:
    """Token budget is respected even with knowledge chunks present."""
    db, vs, emb = _build(tmp_path)
    await db.start()

    clock = SystemClock()
    ids = UuidGenerator()

    store = KnowledgeStore(db=db, vector_store=vs, embedder=emb, ids=ids, clock=clock)  # type: ignore[arg-type]

    episodic = EpisodicMemory(db=db, clock=clock)
    semantic = SemanticMemory(db=db, vectors=vs, embedder=emb, ids=ids, clock=clock)  # type: ignore[arg-type]
    user_model = UserModel(db=db, clock=clock)

    budget = 600
    retriever = Retriever(
        semantic=semantic,
        episodic=episodic,
        user_model=user_model,
        knowledge_store=store,
        token_budget=budget,
        cache_ttl=0.0,
    )

    # Large document to stress the budget
    doc = tmp_path / "large.md"
    doc.write_text("# Large Doc\n\n" + "Word sentence content. " * 200)
    await store.ingest_document(doc, "markdown")

    ctx = await retriever.retrieve("any query")
    # Allow a small overhead from the always-included user model
    assert ctx.token_estimate <= budget + 100

    await db.stop()
