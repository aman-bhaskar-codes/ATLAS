"""The provenance boundary at the write path and the promotion gate.

Provenance is a security boundary, so it is asserted at three independent points:
the value chosen when a caller says nothing (defaults must be conservative), the
value chosen when a caller passes an event through ``from_event`` (a tool result
is data from outside the trust boundary), and the SQL gate that decides what
consolidation is even allowed to read.

The last one matters most. ``promotion_candidates`` is the gate; if it were a
Python ``if`` in the consolidator, a future caller could forget it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from atlas.infra.db import Database
from atlas.memory.episodic import EpisodicMemory
from atlas.memory.types import Episode, EpisodeKind, OriginClass, SessionKind
from tests.fakes import FakeClock

NOW = datetime(2026, 8, 28, 9, 0, 0, tzinfo=UTC)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


@pytest.fixture
async def db(tmp_path: Path):  # type: ignore[no-untyped-def]
    database = Database(tmp_path / "provenance.db")
    await database.start()
    yield database
    await database.stop()


@pytest.fixture
def epi(db: Database, clock: FakeClock) -> EpisodicMemory:
    return EpisodicMemory(db, clock)


async def _record(
    epi: EpisodicMemory,
    *,
    content: str,
    origin: OriginClass = OriginClass.AGENT,
    session: SessionKind = SessionKind.INTERACTIVE,
    salience: float = 0.5,
    minutes_ago: float = 1.0,
) -> int:
    return await epi.record(
        Episode(
            correlation_id="c1",
            ts=NOW - timedelta(minutes=minutes_ago),
            kind=EpisodeKind.MESSAGE,
            content=content,
            origin_class=origin,
            session_kind=session,
            salience=salience,
        )
    )


# ── write path ────────────────────────────────────────────────────────────


async def test_provenance_round_trips_through_the_row(epi: EpisodicMemory) -> None:
    await _record(epi, content="x", origin=OriginClass.UNTRUSTED, session=SessionKind.SUBAGENT)

    (stored,) = await epi.recent(10)
    assert stored.origin_class is OriginClass.UNTRUSTED
    assert stored.session_kind is SessionKind.SUBAGENT


async def test_a_correction_is_the_one_unambiguously_owner_write(epi: EpisodicMemory) -> None:
    # The user telling ATLAS it got something wrong is the highest-trust,
    # highest-value signal there is, so it is written as OWNER with high
    # importance rather than inheriting the AGENT default.
    await epi.record_correction("c9", "no, use Postgres")

    (stored,) = await epi.recent(10)
    assert stored.origin_class is OriginClass.OWNER
    assert stored.importance == 9


@pytest.mark.parametrize(
    ("event_kind", "metadata", "expected"),
    [
        # A tool result is a web page, a file, another service — data from outside
        # the trust boundary, even though ATLAS is what wrote the row.
        ("observation", {}, OriginClass.UNTRUSTED),
        ("tool_result", {}, OriginClass.UNTRUSTED),
        ("message", {"tool": "web.fetch"}, OriginClass.UNTRUSTED),
        ("message", {}, OriginClass.AGENT),
        ("action", {}, OriginClass.AGENT),
    ],
)
def test_origin_classification(
    epi: EpisodicMemory, event_kind: str, metadata: dict[str, object], expected: OriginClass
) -> None:
    assert epi._classify_origin(event_kind, metadata) is expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("cron", SessionKind.CRON),
        ("heartbeat", SessionKind.HEARTBEAT),
        (None, SessionKind.INTERACTIVE),
        ("", SessionKind.INTERACTIVE),
        # An unrecognised value must not raise on the write path; the
        # conservative default is the interactive one the CHECK constraint
        # already accepts.
        ("nonsense", SessionKind.INTERACTIVE),
    ],
)
def test_session_kind_coercion_never_raises(raw: object, expected: SessionKind) -> None:
    assert EpisodicMemory._coerce_session_kind(raw) is expected


# ── the promotion gate ────────────────────────────────────────────────────


async def test_promotion_candidates_admits_only_owner_and_agent(epi: EpisodicMemory) -> None:
    await _record(epi, content="owner", origin=OriginClass.OWNER)
    await _record(epi, content="agent", origin=OriginClass.AGENT)
    await _record(epi, content="untrusted", origin=OriginClass.UNTRUSTED)
    await _record(epi, content="system", origin=OriginClass.SYSTEM)

    contents = {e.content for e in await epi.promotion_candidates()}
    assert contents == {"owner", "agent"}


async def test_promotion_candidates_admits_only_interactive_sessions(epi: EpisodicMemory) -> None:
    await _record(epi, content="interactive", session=SessionKind.INTERACTIVE)
    await _record(epi, content="cron", session=SessionKind.CRON)
    await _record(epi, content="heartbeat", session=SessionKind.HEARTBEAT)
    await _record(epi, content="subagent", session=SessionKind.SUBAGENT)

    contents = {e.content for e in await epi.promotion_candidates()}
    assert contents == {"interactive"}


async def test_promotion_candidates_skips_already_consolidated(epi: EpisodicMemory) -> None:
    eid = await _record(epi, content="done")
    await _record(epi, content="pending")
    await epi.mark_consolidated([eid])

    assert [e.content for e in await epi.promotion_candidates()] == ["pending"]


async def test_promotion_candidates_orders_by_salience(epi: EpisodicMemory) -> None:
    # The limit is a budget; when it bites, the most salient episodes are the
    # ones that should survive it.
    await _record(epi, content="dull", salience=0.1)
    await _record(epi, content="sharp", salience=0.9)

    assert [e.content for e in await epi.promotion_candidates()] == ["sharp", "dull"]


async def test_promotion_candidates_respects_the_limit(epi: EpisodicMemory) -> None:
    for i in range(5):
        await _record(epi, content=f"ep{i}", salience=i / 10)

    assert len(await epi.promotion_candidates(limit=2)) == 2


async def test_episode_promotable_mirrors_the_sql_gate() -> None:
    """The Python mirror is belt-and-braces, so it must agree with the query."""

    def ep(origin: OriginClass, session: SessionKind) -> Episode:
        return Episode(
            correlation_id="c",
            ts=NOW,
            kind=EpisodeKind.MESSAGE,
            content="x",
            origin_class=origin,
            session_kind=session,
        )

    assert ep(OriginClass.OWNER, SessionKind.INTERACTIVE).promotable is True
    assert ep(OriginClass.AGENT, SessionKind.INTERACTIVE).promotable is True
    assert ep(OriginClass.UNTRUSTED, SessionKind.INTERACTIVE).promotable is False
    assert ep(OriginClass.SYSTEM, SessionKind.INTERACTIVE).promotable is False
    assert ep(OriginClass.OWNER, SessionKind.CRON).promotable is False
    assert ep(OriginClass.OWNER, SessionKind.SUBAGENT).promotable is False
