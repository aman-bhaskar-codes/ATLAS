"""A failed migration must leave a resumable database, not a dead one.

THE BUG THIS PINS: ``_apply_migrations`` wrote ``schema_version`` exactly once,
AFTER the whole loop. ``executescript`` commits implicitly, so a failure partway
through left every earlier migration's DDL durably applied while the recorded
version still said 0.

That combination is unrecoverable, not merely inconvenient. On the next boot the
loop restarts from migration 1 — and many of the migrations in ``_MIGRATIONS`` are
``ALTER TABLE ... ADD COLUMN``, which is NOT idempotent: SQLite raises
"duplicate column name". So the database failed to open on every subsequent boot,
with no way forward except deleting the data directory. Writing the version after
each successful step turns that into "fix the script and restart".

These tests drive ``_MIGRATIONS`` directly with a patched, deliberately
non-idempotent sequence — using the real migration list would tie the assertions
to whatever DDL happens to be in the project today.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import aiosqlite
import pytest

from atlas.infra import db as db_module
from atlas.infra.db import Database

# Deliberately non-idempotent, exactly like the real ALTER TABLE migrations:
# replaying step 1 or 2 raises instead of being a no-op.
_M1 = "CREATE TABLE widget (id TEXT PRIMARY KEY);"
_M2 = "ALTER TABLE widget ADD COLUMN label TEXT;"
_M3 = "ALTER TABLE widget ADD COLUMN size INTEGER;"

# Step 2 in a broken state: a mistyped table name, so it fails cleanly having
# applied nothing. NOT a duplicate-column script — in the (_M1, _BROKEN, _M3)
# sequence nothing has added `label` yet, so a second `ADD COLUMN label` would
# succeed and there would be no failure to resume from.
_BROKEN = "ALTER TABLE widget_typo ADD COLUMN label TEXT;"


async def _version(database: Database) -> int:
    cur = await database.conn.execute("SELECT version FROM schema_version LIMIT 1")
    row = await cur.fetchone()
    assert row is not None
    return int(row["version"])


async def _columns(database: Database) -> set[str]:
    cur = await database.conn.execute("PRAGMA table_info(widget)")
    return {row["name"] for row in await cur.fetchall()}


async def test_a_clean_run_records_the_final_version(tmp_path: Path) -> None:
    database = Database(tmp_path / "atlas.db")
    with patch.object(db_module, "_MIGRATIONS", (_M1, _M2, _M3)):
        await database.start()
    try:
        assert await _version(database) == 3
        assert await _columns(database) == {"id", "label", "size"}
    finally:
        await database.stop()


async def test_a_mid_sequence_failure_records_the_steps_that_succeeded(tmp_path: Path) -> None:
    """The version must equal the last step that actually ran — 1, not 0 and not 3."""
    db_path = tmp_path / "atlas.db"
    database = Database(db_path)

    with patch.object(db_module, "_MIGRATIONS", (_M1, _BROKEN, _M3)), pytest.raises(aiosqlite.Error):
        await database.start()

    # The connection survives a failed start (start() raises after connecting), so
    # read the recorded version through it before closing.
    assert await _version(database) == 1, (
        "migration 1's DDL is committed but the version does not say so — the next "
        "boot will replay it and fail on the duplicate column, forever"
    )
    await database.stop()


async def test_a_failed_migration_is_resumable_after_a_fix(tmp_path: Path) -> None:
    """The whole point: fix the script, restart, continue from where it stopped."""
    db_path = tmp_path / "atlas.db"

    first = Database(db_path)
    with patch.object(db_module, "_MIGRATIONS", (_M1, _BROKEN, _M3)), pytest.raises(aiosqlite.Error):
        await first.start()
    await first.stop()

    # Operator fixes migration 2 and restarts.
    second = Database(db_path)
    with patch.object(db_module, "_MIGRATIONS", (_M1, _M2, _M3)):
        await second.start()
    try:
        assert await _version(second) == 3
        assert await _columns(second) == {"id", "label", "size"}, (
            "the fixed run did not resume — it either replayed step 1 or skipped 2 and 3"
        )
    finally:
        await second.stop()


async def test_reopening_an_up_to_date_database_replays_nothing(tmp_path: Path) -> None:
    """Idempotent restart: a second start must not re-run non-idempotent DDL."""
    db_path = tmp_path / "atlas.db"

    with patch.object(db_module, "_MIGRATIONS", (_M1, _M2, _M3)):
        first = Database(db_path)
        await first.start()
        await first.stop()

        second = Database(db_path)
        await second.start()  # would raise "duplicate column name" if it replayed
        try:
            assert await _version(second) == 3
        finally:
            await second.stop()


async def test_the_real_migration_list_applies_end_to_end(tmp_path: Path) -> None:
    """No patching: the shipped migrations must reach their own final version."""
    database = Database(tmp_path / "atlas.db")
    await database.start()
    try:
        assert await _version(database) == len(db_module._MIGRATIONS)
    finally:
        await database.stop()
