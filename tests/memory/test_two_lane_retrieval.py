"""The two-lane read path: what does NOT happen on a normal turn.

The regression these tests guard against is not a wrong answer, it is a cost.
Both ``SemanticMemory.semantic_search`` and ``EpisodicMemory.semantic_search``
embed the query, so an unconditional Lane 2 meant two embedding round-trips per
turn — invisible with a local embedder, a per-turn network tax with a cloud one.

So the central assertion is a call *count* on the embedding-backed fakes, not a
property of the returned context.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from atlas.infra.db import Database
from atlas.memory.curated import MEMORY_KEY, CuratedMemory
from atlas.memory.episodic import EpisodicMemory
from atlas.memory.lanes import LaneOneRecall
from atlas.memory.retrieval import Retriever
from atlas.memory.types import Episode, EpisodeKind, FactKind, OriginClass, SemanticFact
from tests.fakes import FakeClock

NOW = datetime(2026, 8, 28, 9, 0, 0, tzinfo=UTC)


class CountingSem:
    """Stands in for the vector store; every call here is an embedding call."""

    def __init__(self) -> None:
        self.calls = 0

    async def semantic_search(self, query: str, k: int = 15) -> list[SemanticFact]:
        self.calls += 1
        return [
            SemanticFact(
                id="f1",
                text="prefers dark mode",
                kind=FactKind.PREFERENCE,
                created_ts=NOW,
                updated_ts=NOW,
                salience=0.9,
            )
        ]


class CountingEpi:
    """Wraps the real episodic store, counting only the embedding-backed method."""

    def __init__(self, inner: EpisodicMemory) -> None:
        self._inner = inner
        self.semantic_calls = 0

    async def keyword_search(self, terms: list[str], limit: int = 15) -> list[Episode]:
        return await self._inner.keyword_search(terms, limit=limit)

    async def semantic_search(self, query: str, limit: int = 10, min_salience: float = 0.0) -> list[Episode]:
        self.semantic_calls += 1
        return []


class FakeUM:
    async def render(self) -> str:
        return "identity: Aman, BTech CSE"


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


@pytest.fixture
async def db(tmp_path: Path):  # type: ignore[no-untyped-def]
    database = Database(tmp_path / "two_lane.db")
    await database.start()
    yield database
    await database.stop()


async def _store(db: Database, clock: FakeClock, *, content: str, hint: str, importance: int = 8) -> None:
    await EpisodicMemory(db, clock).record(
        Episode(
            correlation_id="c1",
            ts=NOW,
            kind=EpisodeKind.MESSAGE,
            content=content,
            trigger_hint=hint,
            importance=importance,
            origin_class=OriginClass.OWNER,
        )
    )


def _build(db: Database, clock: FakeClock, *, lane_one: bool) -> tuple[Retriever, CountingSem, CountingEpi]:
    sem = CountingSem()
    epi = CountingEpi(EpisodicMemory(db, clock))
    lane = LaneOneRecall(db, clock, CuratedMemory(db, clock)) if lane_one else None
    retriever = Retriever(
        semantic=sem,  # type: ignore[arg-type]
        episodic=epi,  # type: ignore[arg-type]
        user_model=FakeUM(),  # type: ignore[arg-type]
        cache_ttl=0.0,  # caching would mask a second call
        lane_one=lane,
    )
    return retriever, sem, epi


# ── the cost property ─────────────────────────────────────────────────────


async def test_a_lane_one_hit_costs_zero_embedding_calls(db: Database, clock: FakeClock) -> None:
    await _store(db, clock, content="we chose Postgres", hint="database choice")
    retriever, sem, epi = _build(db, clock, lane_one=True)

    ctx = await retriever.retrieve("remind me about the database choice")

    assert sem.calls == 0
    assert epi.semantic_calls == 0
    assert any("Postgres" in e.content for e in ctx.recent_episodes)


async def test_a_novel_question_with_nothing_stored_also_costs_nothing(db: Database, clock: FakeClock) -> None:
    # The common case. Lane 1 finds nothing, but the message is not asking about
    # the past, so escalating would mean paying for embeddings on almost every
    # turn for no benefit.
    retriever, sem, epi = _build(db, clock, lane_one=True)

    await retriever.retrieve("write a python function that reverses a list")

    assert sem.calls == 0
    assert epi.semantic_calls == 0


async def test_explicit_recall_with_no_lane_one_hit_escalates(db: Database, clock: FakeClock) -> None:
    retriever, sem, epi = _build(db, clock, lane_one=True)

    ctx = await retriever.retrieve("what did we decide last week about the rollout")

    assert sem.calls == 1
    assert epi.semantic_calls == 1
    assert any("dark mode" in f.text for f in ctx.facts)


async def test_without_lane_one_the_old_always_escalate_path_runs(db: Database, clock: FakeClock) -> None:
    # Backward compatibility is exact: existing construction sites that pass no
    # lane_one must behave byte-for-byte as before.
    retriever, sem, epi = _build(db, clock, lane_one=False)

    ctx = await retriever.retrieve("write a python function that reverses a list")

    assert sem.calls == 1
    assert epi.semantic_calls == 1
    assert ctx.curated == ""


async def test_set_lane_one_enables_the_fast_path_after_construction(db: Database, clock: FakeClock) -> None:
    await _store(db, clock, content="we chose Postgres", hint="database choice")
    retriever, sem, epi = _build(db, clock, lane_one=False)
    retriever.set_lane_one(LaneOneRecall(db, clock, CuratedMemory(db, clock)))

    await retriever.retrieve("remind me about the database choice")

    assert sem.calls == 0
    assert epi.semantic_calls == 0


# ── the curated tier ──────────────────────────────────────────────────────


async def test_the_curated_tier_is_always_present_and_never_budgeted_away(db: Database, clock: FakeClock) -> None:
    curated = CuratedMemory(db, clock)
    await curated.create_if_absent(MEMORY_KEY, "- always ship behind a flag\n")

    sem = CountingSem()
    epi = CountingEpi(EpisodicMemory(db, clock))
    retriever = Retriever(
        semantic=sem,  # type: ignore[arg-type]
        episodic=epi,  # type: ignore[arg-type]
        user_model=FakeUM(),  # type: ignore[arg-type]
        token_budget=1,  # everything else is squeezed out
        cache_ttl=0.0,
        lane_one=LaneOneRecall(db, clock, curated),
    )

    ctx = await retriever.retrieve("anything at all")

    assert "always ship behind a flag" in ctx.curated
    assert ctx.facts == ()
    # Counted even though it is not budgeted, so the reported estimate matches
    # what the planner actually receives.
    assert ctx.token_estimate > 0
    assert "always ship behind a flag" in ctx.render()
