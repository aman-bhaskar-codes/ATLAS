"""The semantic cache must never fail the completion it was trying to save.

WHY this file exists: embeddings moved from a local model to a free cloud API
(``CloudEmbedder``), which makes "no embedding available" an ordinary condition —
no key configured, free quota exhausted, transient 5xx. ``SemanticCache`` sits on
the hot path of *every* non-streaming completion, so an unguarded ``embed()``
there turns a cache optimisation into a hard outage of the model gateway. These
tests pin the degradation: a miss, a warning, and the exact-hash path still live.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from atlas.infra.db import Database
from atlas.intelligence.cache import SemanticCache
from atlas.intelligence.contracts import InferenceRequest, InferenceResponse, Message


class BrokenEmbedder:
    """An embedder that always fails, like a cloud embedder with no key."""

    def __init__(self, error: Exception | None = None) -> None:
        self.calls = 0
        self._error = error or RuntimeError("CloudEmbedder has no API key")

    async def embed(self, text: str) -> list[float]:
        self.calls += 1
        raise self._error


class GoodEmbedder:
    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, text: str) -> list[float]:
        self.calls += 1
        return [0.1] * 8


class RecordingVectors:
    """Minimal vector store; records what the cache asked it to do."""

    def __init__(self, hits: list[Any] | None = None, upsert_error: Exception | None = None) -> None:
        self.upserts: list[tuple[str, str, list[float]]] = []
        self.queries: list[list[float]] = []
        self._hits = hits or []
        self._upsert_error = upsert_error

    async def upsert(self, ref: str, text: str, embedding: list[float]) -> None:
        if self._upsert_error is not None:
            raise self._upsert_error
        self.upserts.append((ref, text, embedding))

    async def query(self, embedding: list[float], k: int) -> list[Any]:
        self.queries.append(embedding)
        return self._hits


class ExplodingVectors(RecordingVectors):
    async def query(self, embedding: list[float], k: int) -> list[Any]:
        raise RuntimeError("chroma is wedged")


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "cache.db")
    await database.start()
    yield database
    await database.stop()


def _req(text: str = "what is the capital of France?") -> InferenceRequest:
    return InferenceRequest(correlation_id="corr-cache-1", messages=[Message(role="user", content=text)])


def _resp(text: str = "Paris.") -> InferenceResponse:
    return InferenceResponse(text=text, model_id="glm-5.2-free", provider="openrouter")


class TestGetDegrades:
    async def test_embed_failure_is_a_miss_not_a_raise(self, db: Database) -> None:
        embedder = BrokenEmbedder()
        vectors = RecordingVectors()
        cache = SemanticCache(db, vectors, embedder)

        assert await cache.get(_req()) is None
        assert embedder.calls == 1, "the embedder should still be tried once"
        assert vectors.queries == [], "no vector query without an embedding"

    async def test_vector_query_failure_is_a_miss(self, db: Database) -> None:
        cache = SemanticCache(db, ExplodingVectors(), GoodEmbedder())
        assert await cache.get(_req()) is None

    async def test_exact_hash_hit_never_reaches_the_embedder(self, db: Database) -> None:
        embedder = BrokenEmbedder()
        cache = SemanticCache(db, RecordingVectors(), embedder)

        # put() with a broken embedder still records the row...
        await cache.put(_req(), _resp("Paris."))
        # ...so the exact-hash fast path hits without embedding anything.
        hit = await cache.get(_req())
        assert hit is not None
        assert hit.text == "Paris."
        assert embedder.calls == 1, "only put() tried to embed; the exact hit short-circuits"

    async def test_streaming_requests_are_not_cached(self, db: Database) -> None:
        embedder = BrokenEmbedder()
        cache = SemanticCache(db, RecordingVectors(), embedder)
        req = InferenceRequest(
            correlation_id="corr-cache-2",
            messages=[Message(role="user", content="stream me")],
            stream=True,
        )
        assert await cache.get(req) is None
        assert embedder.calls == 0


class TestPutDegrades:
    async def test_put_survives_embed_failure_and_skips_the_upsert(self, db: Database) -> None:
        vectors = RecordingVectors()
        cache = SemanticCache(db, vectors, BrokenEmbedder())

        await cache.put(_req(), _resp())  # must not raise

        assert vectors.upserts == [], "no vector written without an embedding"
        cur = await db.conn.execute("SELECT COUNT(*) AS n FROM semantic_cache")
        row = await cur.fetchone()
        assert row["n"] == 1, "the exact-hash row is still worth keeping"

    async def test_put_survives_a_failing_vector_store(self, db: Database) -> None:
        vectors = RecordingVectors(upsert_error=RuntimeError("chroma is wedged"))
        cache = SemanticCache(db, vectors, GoodEmbedder())

        await cache.put(_req(), _resp())  # must not raise

        cur = await db.conn.execute("SELECT COUNT(*) AS n FROM semantic_cache")
        row = await cur.fetchone()
        assert row["n"] == 1

    async def test_healthy_path_still_embeds_and_upserts(self, db: Database) -> None:
        embedder = GoodEmbedder()
        vectors = RecordingVectors()
        cache = SemanticCache(db, vectors, embedder)

        await cache.put(_req(), _resp())

        assert embedder.calls == 1
        assert len(vectors.upserts) == 1
        ref, text, embedding = vectors.upserts[0]
        assert "capital of France" in text
        assert embedding == [0.1] * 8
        cur = await db.conn.execute("SELECT embedding_ref FROM semantic_cache")
        row = await cur.fetchone()
        assert row["embedding_ref"] == ref, "the DB row and the vector must share the ref"
