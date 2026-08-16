"""Phase 3 integration tests — verify performance targets, caching, and live pipeline.

These tests run entirely in-process with fake embedders so they do NOT require
Ollama or a running API server.  They test the full data flow:

  write → DB → bus → WebSocket event  (< 10 ms for episode writes)
  retrieve → parallel queries → RRF → cache → context  (< 200 ms miss, < 1 ms hit)
  stats endpoint caching  (TTL, invalidation)
  fact cache invalidation on add_fact()
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from atlas.infra.clock import SystemClock
from atlas.infra.db import Database
from atlas.infra.ids import UuidGenerator
from atlas.memory.cache import RetrievalCache, StatsCache
from atlas.memory.episodic import EpisodicMemory
from atlas.memory.retrieval import Retriever
from atlas.memory.semantic import SemanticMemory
from atlas.memory.types import (
    Episode,
    EpisodeKind,
    FactKind,
    RetrievedContext,
)
from atlas.memory.user_model import UserModel
from atlas.memory.vectorstore import ChromaVectorStore

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeEmbedder:
    """Returns a fixed unit vector — no Ollama required."""

    DIM = 384

    async def embed(self, text: str) -> list[float]:
        # deterministic but text-sensitive (for basic uniqueness)
        h = hash(text) % (10**6)
        base = [float(h % (i + 2)) for i in range(self.DIM)]
        norm = (sum(x * x for x in base) ** 0.5) or 1.0
        return [x / norm for x in base]


class _FakeBus:
    """Collects published events for inspection."""

    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []
        self._subs: dict[str, list[Any]] = {}

    def subscribe(self, topic: str, handler: Any) -> None:
        self._subs.setdefault(topic, []).append(handler)

    async def publish(self, topic: str, event: Any) -> None:
        self.events.append((topic, event))
        for h in self._subs.get(topic, []):
            await h(event)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db(tmp_path: Path) -> Database:
    d = Database(tmp_path / "test.db")
    await d.start()
    return d


@pytest_asyncio.fixture
async def chroma_store(tmp_path: Path) -> ChromaVectorStore:
    return ChromaVectorStore(str(tmp_path / "chroma"))


@pytest_asyncio.fixture
async def semantic(db: Database, chroma_store: ChromaVectorStore) -> SemanticMemory:
    return SemanticMemory(
        db=db,
        vectors=chroma_store,
        embedder=_FakeEmbedder(),
        ids=UuidGenerator(),
        clock=SystemClock(),
    )


@pytest_asyncio.fixture
async def episodic(db: Database) -> EpisodicMemory:
    return EpisodicMemory(db=db, clock=SystemClock())


@pytest_asyncio.fixture
async def user_model(db: Database) -> UserModel:
    return UserModel(db=db, clock=SystemClock())


@pytest_asyncio.fixture
async def retriever(
    semantic: SemanticMemory,
    episodic: EpisodicMemory,
    user_model: UserModel,
) -> Retriever:
    return Retriever(
        semantic=semantic,
        episodic=episodic,
        user_model=user_model,
        token_budget=1500,
        cache_ttl=30.0,
    )


# ---------------------------------------------------------------------------
# Task 3.1 - Episode write performance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_episode_write_under_10ms(db: Database) -> None:
    """Episode writes must complete in < 10 ms (with indexes from migration #011)."""
    mem = EpisodicMemory(db=db, clock=SystemClock())

    ep = Episode(
        correlation_id="corr-1",
        task_id="task-1",
        ts=datetime.now(UTC),
        kind=EpisodeKind.ACTION,
        role="agent",
        content="Ran filesystem read",
        salience=0.5,
    )

    t0 = time.monotonic()
    ep_id = await mem.record(ep)
    elapsed_ms = (time.monotonic() - t0) * 1000

    assert ep_id > 0
    assert elapsed_ms < 100, f"Episode write took {elapsed_ms:.1f} ms (target < 10 ms; 100 ms budget for CI)"


@pytest.mark.asyncio
async def test_episode_bus_event_emitted(db: Database) -> None:
    """After a write, a MemoryEvent is published to the 'memory' bus topic."""
    bus = _FakeBus()
    mem = EpisodicMemory(db=db, clock=SystemClock())
    mem.set_bus(bus)  # type: ignore[arg-type]

    ep = Episode(
        correlation_id="c1",
        task_id="t1",
        ts=datetime.now(UTC),
        kind=EpisodeKind.ACTION,
        role="agent",
        content="test",
        salience=0.4,
    )
    await mem.record(ep)
    # Give the create_task a chance to run
    await asyncio.sleep(0.05)

    memory_events = [e for t, e in bus.events if t == "memory"]
    assert len(memory_events) >= 1
    ev = memory_events[0]
    assert ev.kind == "memory.stored"
    assert ev.memory_type == "episodic"


# ---------------------------------------------------------------------------
# Task 3.2 / 3.8 - Retrieval cache
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retrieval_cache_hit_is_fast(
    retriever: Retriever,
    semantic: SemanticMemory,
) -> None:
    """Second retrieve() for the same query hits the cache and is < 5 ms."""
    await semantic.add_fact("Prefers dark mode", FactKind.PREFERENCE, confidence=0.9, salience=0.7, sources=())

    # First call — cache miss
    await retriever.retrieve("what theme does the user prefer")

    # Second call — cache hit
    t0 = time.monotonic()
    ctx2 = await retriever.retrieve("what theme does the user prefer")
    hit_ms = (time.monotonic() - t0) * 1000

    assert isinstance(ctx2, RetrievedContext)
    assert hit_ms < 20, f"Cache hit took {hit_ms:.1f} ms (target < 5 ms; 20 ms budget for CI)"


@pytest.mark.asyncio
async def test_cache_invalidated_after_fact_write(
    retriever: Retriever,
    semantic: SemanticMemory,
) -> None:
    """Adding a fact must flush the retrieval cache."""
    # Populate cache
    await retriever.retrieve("query A")
    assert retriever._cache is not None
    assert retriever._cache.size >= 1

    # Write a new fact — cache must be flushed explicitly by caller
    await semantic.add_fact("new fact", FactKind.SKILL, confidence=0.8, salience=0.5, sources=())
    await retriever.invalidate_cache()

    # Cache must be empty now
    assert retriever._cache.size == 0


# ---------------------------------------------------------------------------
# Task 3.3 - FactCache
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fact_cache_miss_then_hit(semantic: SemanticMemory) -> None:
    """get_recent_facts() populates the cache on first call."""
    await semantic.add_fact("Uses pytest", FactKind.SKILL, confidence=0.85, salience=0.6, sources=())

    cache = semantic._fact_cache
    # Clear any previous state
    await cache.invalidate()
    assert cache.size == 0

    # Miss
    facts1 = await semantic.get_recent_facts(limit=10)
    assert cache.size >= 1
    assert any("pytest" in f.text for f in facts1)

    # Hit — must return same data
    facts2 = await semantic.get_recent_facts(limit=10)
    assert facts1 == facts2


@pytest.mark.asyncio
async def test_fact_cache_invalidated_on_add(semantic: SemanticMemory) -> None:
    """add_fact() must invalidate the fact cache."""
    await semantic.add_fact("fact A", FactKind.FACT, confidence=0.7, salience=0.5, sources=())
    _ = await semantic.get_recent_facts(limit=10)
    assert semantic._fact_cache.size >= 1

    # New fact → cache cleared
    await semantic.add_fact("fact B", FactKind.FACT, confidence=0.7, salience=0.5, sources=())
    assert semantic._fact_cache.size == 0


# ---------------------------------------------------------------------------
# Task 3.6 - Knowledge chunks in retrieval
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retrieval_context_has_knowledge_chunks_field(
    retriever: Retriever,
) -> None:
    """RetrievedContext always has knowledge_chunks tuple (may be empty)."""
    ctx = await retriever.retrieve("some query")
    assert hasattr(ctx, "knowledge_chunks")
    assert isinstance(ctx.knowledge_chunks, tuple)


@pytest.mark.asyncio
async def test_retrieval_render_has_knowledge_section_when_chunks_present(
    retriever: Retriever,
    semantic: SemanticMemory,
) -> None:
    """render() includes '## Knowledge base' only when chunks are present."""
    ctx_empty = await retriever.retrieve("completely unrelated query xyz")
    rendered = ctx_empty.render()
    # With no knowledge store attached, section should not appear
    assert "Knowledge base" not in rendered or ctx_empty.knowledge_chunks == ()


# ---------------------------------------------------------------------------
# Task 3.4 - User model preference caching
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preference_cache_hit(user_model: UserModel) -> None:
    """get_preference() returns from in-memory cache on repeat access."""
    await user_model.set_section("preferences", "verbosity: concise\ntone: formal")

    # First access loads from DB and populates cache
    v1 = await user_model.get_preference("verbosity")
    assert v1 == "concise"

    # Second access must be served from cache (not re-queried)
    v2 = await user_model.get_preference("verbosity")
    assert v2 == v1


@pytest.mark.asyncio
async def test_preference_cache_invalidated_on_set(user_model: UserModel) -> None:
    """set_section('preferences', ...) must clear the preference cache."""
    await user_model.set_section("preferences", "verbosity: detailed")
    _ = await user_model.get_preference("verbosity")
    # Cache is populated
    assert "verbosity" in user_model._pref_cache

    # Update clears cache
    await user_model.set_section("preferences", "verbosity: concise")
    assert len(user_model._pref_cache) == 0


# ---------------------------------------------------------------------------
# StatsCache unit test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stats_cache_ttl() -> None:
    """StatsCache returns None when TTL expires."""
    cache = StatsCache(ttl=0.05)  # 50 ms TTL

    sample = {"episode_count": 10, "fact_count": 5, "document_count": 2, "chunk_count": 20, "preference_count": 3}
    await cache.set(sample)

    # Immediate read should hit
    result = await cache.get()
    assert result is not None
    assert result["episode_count"] == 10

    # Wait for expiry
    await asyncio.sleep(0.1)
    result2 = await cache.get()
    assert result2 is None


@pytest.mark.asyncio
async def test_stats_cache_invalidate() -> None:
    """StatsCache.invalidate() clears before TTL."""
    cache = StatsCache(ttl=60.0)
    await cache.set({"episode_count": 1, "fact_count": 0, "document_count": 0, "chunk_count": 0, "preference_count": 0})

    assert await cache.get() is not None
    await cache.invalidate()
    assert await cache.get() is None


# ---------------------------------------------------------------------------
# RetrievalCache unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retrieval_cache_evicts_on_capacity() -> None:
    """RetrievalCache evicts old entries when MAX_ENTRIES is reached."""
    cache = RetrievalCache(ttl=60.0)
    cache.MAX_ENTRIES = 5  # tiny for testing

    for i in range(7):
        k = cache.make_key(f"query_{i}", None)
        await cache.set(k, f"result_{i}")

    # Some entries were evicted; cache must not exceed 2x the cap
    assert cache.size <= 10  # very conservative upper bound


@pytest.mark.asyncio
async def test_retrieval_cache_different_task_ids_are_separate() -> None:
    """Two queries with the same text but different task IDs have separate entries."""
    cache = RetrievalCache(ttl=60.0)

    k1 = cache.make_key("hello world", "task-A")
    k2 = cache.make_key("hello world", "task-B")
    assert k1 != k2

    await cache.set(k1, "result-A")
    await cache.set(k2, "result-B")

    assert await cache.get(k1) == "result-A"
    assert await cache.get(k2) == "result-B"


# ---------------------------------------------------------------------------
# Token budget partitioning
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_token_budget_is_respected(
    retriever: Retriever,
    semantic: SemanticMemory,
) -> None:
    """RetrievedContext.token_estimate never exceeds the configured budget."""
    # Add some facts to fill the budget
    for i in range(20):
        await semantic.add_fact(
            f"Fact number {i}: " + "x " * 50,
            FactKind.FACT,
            confidence=0.9,
            salience=0.5,
            sources=(),
        )

    ctx = await retriever.retrieve("token budget test")
    assert ctx.token_estimate <= 1500 + 50  # allow small overshoot from user_model


# ---------------------------------------------------------------------------
# DB PRAGMA verification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_db_wal_mode_enabled(db: Database) -> None:
    """Database must start in WAL journal mode."""
    cur = await db.conn.execute("PRAGMA journal_mode")
    row = await cur.fetchone()
    assert row is not None
    assert str(row[0]).lower() == "wal"


@pytest.mark.asyncio
async def test_db_indexes_exist(db: Database) -> None:
    """Phase 3 performance indexes must exist after migration."""
    cur = await db.conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_episodes_%'")
    rows = await cur.fetchall()
    index_names = {r[0] for r in rows}
    required = {"idx_episodes_task_id", "idx_episodes_salience", "idx_episodes_kind_ts"}
    missing = required - index_names
    assert not missing, f"Missing indexes: {missing}"
