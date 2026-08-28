"""Migration 029 + the curated tier's compare-and-swap discipline.

These tests are about *structural* guarantees rather than behaviour: that the
provenance columns exist with the right defaults, that the CHECK constraint is a
real write error and not a convention, and that a stale writer loses.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from aiosqlite import IntegrityError

from atlas.infra.db import _MIGRATIONS, Database
from atlas.memory.curated import MEMORY_KEY, USER_KEY, CuratedMemory, content_hash
from tests.fakes import FakeClock

NOW = datetime(2026, 8, 28, 9, 0, 0, tzinfo=UTC)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


@pytest.fixture
async def db(tmp_path: Path):  # type: ignore[no-untyped-def]
    database = Database(tmp_path / "curated.db")
    await database.start()
    yield database
    await database.stop()


# ── migration 029 ────────────────────────────────────────────────────────


async def test_schema_version_matches_migration_count(db: Database) -> None:
    cur = await db.conn.execute("SELECT version FROM schema_version LIMIT 1")
    row = await cur.fetchone()
    assert row is not None
    assert row[0] == len(_MIGRATIONS)


async def test_episodes_has_provenance_and_recall_columns(db: Database) -> None:
    cur = await db.conn.execute("PRAGMA table_info(episodes)")
    cols = {r[1] for r in await cur.fetchall()}
    assert {"origin_class", "session_kind", "importance", "trigger_hint"} <= cols


async def test_provenance_defaults_are_the_conservative_ones(db: Database) -> None:
    # An insert by code that has not been taught about provenance must land as
    # AGENT/INTERACTIVE — recallable, but never impersonating the owner.
    await db.conn.execute(
        "INSERT INTO episodes(correlation_id, step, ts, kind, content, salience, consolidated, tokens) "
        "VALUES ('c1', 0, ?, 'message', 'hi', 0.5, 0, 1)",
        (NOW.isoformat(),),
    )
    await db.conn.commit()
    cur = await db.conn.execute("SELECT origin_class, session_kind, importance, trigger_hint FROM episodes")
    row = await cur.fetchone()
    assert row is not None
    assert tuple(row) == ("agent", "interactive", None, None)


@pytest.mark.parametrize(
    ("column", "value"),
    [("origin_class", "owner_ish"), ("session_kind", "batch")],
)
async def test_check_constraint_rejects_unknown_provenance(db: Database, column: str, value: str) -> None:
    # The point of the CHECK: an invalid provenance value is a hard write error,
    # not something a prose-following model can talk its way around.
    with pytest.raises(IntegrityError):
        await db.conn.execute(
            f"INSERT INTO episodes(correlation_id, step, ts, kind, content, salience, consolidated, tokens, {column}) "
            "VALUES ('c2', 0, ?, 'message', 'hi', 0.5, 0, 1, ?)",
            (NOW.isoformat(), value),
        )
        await db.conn.commit()


async def test_curated_and_intent_tables_exist(db: Database) -> None:
    cur = await db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    names = {r[0] for r in await cur.fetchall()}
    assert {"curated_memory", "standing_intents"} <= names


# ── curated tier ─────────────────────────────────────────────────────────


async def test_create_if_absent_is_idempotent(db: Database, clock: FakeClock) -> None:
    curated = CuratedMemory(db, clock)
    first = await curated.create_if_absent(MEMORY_KEY, "seed")
    second = await curated.create_if_absent(MEMORY_KEY, "ignored")
    assert first.version == second.version == 1
    assert second.content == "seed"


async def test_swap_wins_on_matching_hash(db: Database, clock: FakeClock) -> None:
    curated = CuratedMemory(db, clock)
    doc = await curated.create_if_absent(MEMORY_KEY, "old")
    assert await curated.swap(MEMORY_KEY, new_content="new", expected_hash=doc.content_hash) is True

    after = await curated.get(MEMORY_KEY)
    assert after is not None
    assert after.content == "new"
    assert after.version == 2
    assert after.pre_image == "old"
    assert after.pre_image_hash == content_hash("old")


async def test_swap_loses_on_stale_hash(db: Database, clock: FakeClock) -> None:
    # This is the lost-update guard: consolidation captured a hash, a live turn
    # wrote, and the sweep must abort rather than clobber the newer content.
    curated = CuratedMemory(db, clock)
    doc = await curated.create_if_absent(MEMORY_KEY, "old")
    assert await curated.swap(MEMORY_KEY, new_content="from-live-turn", expected_hash=doc.content_hash) is True

    assert await curated.swap(MEMORY_KEY, new_content="from-stale-sweep", expected_hash=doc.content_hash) is False
    after = await curated.get(MEMORY_KEY)
    assert after is not None
    assert after.content == "from-live-turn"


async def test_append_never_removes_an_existing_line(db: Database, clock: FakeClock) -> None:
    curated = CuratedMemory(db, clock)
    await curated.create_if_absent(MEMORY_KEY, "- first\n")
    assert await curated.append(MEMORY_KEY, "- second") is True

    after = await curated.get(MEMORY_KEY)
    assert after is not None
    assert after.content == "- first\n- second\n"


async def test_append_rejects_blank(db: Database, clock: FakeClock) -> None:
    curated = CuratedMemory(db, clock)
    await curated.create_if_absent(MEMORY_KEY, "- first\n")
    assert await curated.append(MEMORY_KEY, "   \n") is False


async def test_revert_restores_the_pre_image(db: Database, clock: FakeClock) -> None:
    curated = CuratedMemory(db, clock)
    doc = await curated.create_if_absent(MEMORY_KEY, "good")
    await curated.swap(MEMORY_KEY, new_content="bad merge", expected_hash=doc.content_hash)

    assert await curated.revert(MEMORY_KEY) is True
    after = await curated.get(MEMORY_KEY)
    assert after is not None
    assert after.content == "good"


async def test_revert_without_a_pre_image_is_a_no_op(db: Database, clock: FakeClock) -> None:
    curated = CuratedMemory(db, clock)
    await curated.create_if_absent(MEMORY_KEY, "fresh")
    assert await curated.revert(MEMORY_KEY) is False


async def test_bootstrap_renders_only_non_empty_surfaces(db: Database, clock: FakeClock) -> None:
    curated = CuratedMemory(db, clock)
    await curated.create_if_absent(MEMORY_KEY, "durable knowledge")
    await curated.create_if_absent(USER_KEY, "   ")

    rendered = await curated.bootstrap()
    assert "## MEMORY" in rendered
    assert "durable knowledge" in rendered
    # An empty surface is absent rather than a placeholder the model might read
    # as content.
    assert "## USER" not in rendered


async def test_bootstrap_on_a_fresh_install_is_empty(db: Database, clock: FakeClock) -> None:
    assert await CuratedMemory(db, clock).bootstrap() == ""
