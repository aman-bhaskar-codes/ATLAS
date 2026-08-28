"""Prospective memory: matching, fire budget, cooldown, expiry, scope.

The interesting assertions here are all about an intent *declining* to fire. A
standing intent that matches is easy; one that knows when to stop is the whole
design.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from atlas.infra.db import Database
from atlas.memory.intents import IntentStore, keyword_matches
from atlas.memory.types import IntentStatus
from tests.fakes import FakeClock

NOW = datetime(2026, 8, 28, 9, 0, 0, tzinfo=UTC)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


@pytest.fixture
async def db(tmp_path: Path):  # type: ignore[no-untyped-def]
    database = Database(tmp_path / "intents.db")
    await database.start()
    yield database
    await database.stop()


@pytest.fixture
def store(db: Database, clock: FakeClock) -> IntentStore:
    return IntentStore(db, clock)


# ── keyword matching ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("keyword", "message", "expected"),
    [
        ("visa", "did the visa arrive?", True),
        ("visa", "Visa Application", True),
        # Word-bounded, so a short keyword does not fire on a longer word that
        # merely contains it — the failure mode that makes keyword triggers
        # useless in practice.
        ("art", "let's start now", False),
        ("art", "modern art show", True),
        ("tax return", "my tax return is late", True),
        ("tax return", "my tax is late", False),
        ("", "anything", False),
        ("   ", "anything", False),
    ],
)
def test_keyword_matches(keyword: str, message: str, expected: bool) -> None:
    assert keyword_matches(keyword, message) is expected


def test_keyword_with_regex_metacharacters_is_literal(store: IntentStore) -> None:
    # A keyword is data, not a pattern: an unescaped "." would match anything.
    assert keyword_matches("c++", "I use c++ daily") is True
    assert keyword_matches("a.c", "abc") is False


# ── lifecycle ────────────────────────────────────────────────────────────


async def test_create_defaults_to_armed(store: IntentStore) -> None:
    intent = await store.create("ask about the visa", ("visa",))
    assert intent.status is IntentStatus.ARMED
    assert intent.fire_count == 0
    assert intent.budget_remaining == 3


async def test_pending_intents_still_match(store: IntentStore) -> None:
    # PENDING is "not yet acted on", not "invisible" — both live statuses are
    # scanned so an intent created ahead of time is not silently inert.
    await store.create("later", ("visa",), status=IntentStatus.PENDING)
    assert len(await store.match("about my visa")) == 1


async def test_match_requires_a_keyword(store: IntentStore) -> None:
    # A keyword-less intent would attach itself to every turn.
    await store.create("vague hope", ())
    assert await store.match("literally anything") == []


async def test_match_respects_channel_and_sender_scope(store: IntentStore) -> None:
    await store.create("only in slack", ("deploy",), channel_scope="slack")
    assert await store.match("deploy now", channel="email") == []
    assert len(await store.match("deploy now", channel="slack")) == 1

    await store.create("only from ana", ("release",), sender_scope="ana")
    assert await store.match("release now", sender="bo") == []
    assert len(await store.match("release now", sender="ana")) == 1


async def test_fire_charges_the_budget_and_starts_a_cooldown(store: IntentStore, clock: FakeClock) -> None:
    intent = await store.create("ask about the visa", ("visa",), fire_budget=2)
    assert await store.mark_fired(intent.id, cooldown_s=900) is True

    after = await store.get(intent.id)
    assert after is not None
    assert after.fire_count == 1
    # Still in budget, so it goes back to ARMED rather than sitting in FIRED
    # where the active scan would never see it again.
    assert after.status is IntentStatus.ARMED
    assert after.cooldown_until == NOW + timedelta(seconds=900)

    # ...but it must not fire again during the cooldown.
    assert await store.match("visa update?") == []

    clock._now = NOW + timedelta(seconds=901)
    assert len(await store.match("visa update?")) == 1


async def test_exhausted_budget_ends_the_intent(store: IntentStore, clock: FakeClock) -> None:
    intent = await store.create("nag once", ("visa",), fire_budget=1)
    await store.mark_fired(intent.id, cooldown_s=0)

    after = await store.get(intent.id)
    assert after is not None
    assert after.status is IntentStatus.DONE
    assert after.budget_remaining == 0

    clock._now = NOW + timedelta(days=1)
    assert await store.match("visa update?") == []
    assert await store.active() == []


async def test_mark_fired_on_a_missing_intent_is_false(store: IntentStore) -> None:
    assert await store.mark_fired("nope") is False


async def test_expiry_stops_matching_before_the_sweep_runs(store: IntentStore, clock: FakeClock) -> None:
    # The read is the correctness guarantee; the sweep is bookkeeping. So an
    # expired intent must stop matching immediately, with no sweep in between.
    await store.create("time-boxed", ("visa",), expires_at=NOW + timedelta(hours=1))
    assert len(await store.match("visa?")) == 1

    clock._now = NOW + timedelta(hours=2)
    assert await store.match("visa?") == []


async def test_expire_due_settles_stored_status(store: IntentStore, clock: FakeClock) -> None:
    intent = await store.create("time-boxed", ("visa",), expires_at=NOW + timedelta(hours=1))
    clock._now = NOW + timedelta(hours=2)

    assert await store.expire_due() == 1
    after = await store.get(intent.id)
    assert after is not None
    assert after.status is IntentStatus.EXPIRED
    # Idempotent: a second sweep has nothing left to settle.
    assert await store.expire_due() == 0


async def test_set_status_cancels(store: IntentStore) -> None:
    intent = await store.create("never mind", ("visa",))
    assert await store.set_status(intent.id, IntentStatus.CANCELLED) is True
    assert await store.match("visa?") == []
    assert await store.set_status("nope", IntentStatus.CANCELLED) is False


async def test_keywords_survive_the_json_round_trip(store: IntentStore) -> None:
    intent = await store.create("multi", ("visa", "passport", "tax return"))
    fetched = await store.get(intent.id)
    assert fetched is not None
    assert fetched.keywords == ("visa", "passport", "tax return")


async def test_active_orders_oldest_first(store: IntentStore, clock: FakeClock) -> None:
    await store.create("first", ("visa",), intent_id="a")
    clock._now = NOW + timedelta(minutes=1)
    await store.create("second", ("visa",), intent_id="b")

    assert [i.id for i in await store.active()] == ["a", "b"]
