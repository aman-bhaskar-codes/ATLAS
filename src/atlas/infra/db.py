"""SQLite persistence substrate.

WHY one connection: single-user, single-process; WAL mode handles our
concurrency. WHY numbered migrations now: schema evolution across 13+ phases
must be ordered and inspectable, never ad-hoc. Phase 1 creates audit + queue
placeholder tables; memory/KG tables arrive as later-numbered migrations.
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite

from atlas.infra.errors import FatalError
from atlas.infra.logging import get_logger

_log = get_logger("atlas.db")

_MIGRATIONS: tuple[str, ...] = (
    # 001 — audit (two-table split: compact index + fat payloads)
    """
    CREATE TABLE IF NOT EXISTS audit_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        correlation_id TEXT NOT NULL,
        ts TEXT NOT NULL,
        actor TEXT NOT NULL,
        action TEXT NOT NULL,
        tool TEXT,
        tier INTEGER,
        decision TEXT,
        outcome TEXT,
        payload_id INTEGER,
        cost_tokens INTEGER NOT NULL DEFAULT 0,
        cost_usd REAL NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_audit_corr ON audit_events(correlation_id);
    CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_events(ts);
    CREATE TABLE IF NOT EXISTS payloads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        body TEXT NOT NULL
    );
    """,
    # 002 — durable task queue placeholder (activated Phase 4; created now for stability)
    """
    CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY,
        source TEXT,
        state TEXT NOT NULL,
        payload TEXT NOT NULL,
        idempotency_key TEXT UNIQUE,
        attempts INTEGER NOT NULL DEFAULT 0,
        not_before TEXT,
        created_ts TEXT NOT NULL,
        updated_ts TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS dead_letters (
        id TEXT PRIMARY KEY, task_id TEXT, reason TEXT,
        last_error TEXT, payload TEXT, ts TEXT NOT NULL
    );
    """,
    # 003 — memory (Phase 3: episodic, semantic, user-model, consolidation, archive)
    """
    CREATE TABLE IF NOT EXISTS episodes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        correlation_id TEXT NOT NULL,
        task_id TEXT,
        step INTEGER NOT NULL DEFAULT 0,
        ts TEXT NOT NULL,
        kind TEXT NOT NULL,
        role TEXT,
        content TEXT NOT NULL,
        tool TEXT,
        outcome TEXT,
        salience REAL NOT NULL DEFAULT 0,
        consolidated INTEGER NOT NULL DEFAULT 0,
        tokens INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_ep_corr ON episodes(correlation_id);
    CREATE INDEX IF NOT EXISTS idx_ep_ts ON episodes(ts);
    CREATE INDEX IF NOT EXISTS idx_ep_consolidated
        ON episodes(consolidated, salience);

    CREATE TABLE IF NOT EXISTS semantic_facts (
        id TEXT PRIMARY KEY,
        version INTEGER NOT NULL DEFAULT 1,
        text TEXT NOT NULL,
        kind TEXT NOT NULL,
        confidence REAL NOT NULL DEFAULT 0.5,
        salience REAL NOT NULL DEFAULT 0.5,
        source_episode_ids TEXT,
        superseded_by TEXT,
        created_ts TEXT NOT NULL,
        updated_ts TEXT NOT NULL,
        embedding_ref TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_sem_kind
        ON semantic_facts(kind, superseded_by);

    CREATE TABLE IF NOT EXISTS user_model (
        section TEXT PRIMARY KEY,
        content TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 1,
        updated_ts TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS consolidation_proposals (
        id TEXT PRIMARY KEY,
        created_ts TEXT NOT NULL,
        kind TEXT NOT NULL,
        payload TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending'
    );

    CREATE TABLE IF NOT EXISTS memory_archive (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        period TEXT NOT NULL,
        summary TEXT NOT NULL,
        episode_count INTEGER NOT NULL,
        created_ts TEXT NOT NULL
    );
    """,
    # 004 — identity platform (Phase 6.2)
    """
    CREATE TABLE IF NOT EXISTS secrets (
        id TEXT PRIMARY KEY,
        ciphertext TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS identities (
        id TEXT PRIMARY KEY,
        kind TEXT,
        provider_hint TEXT,
        expires_at TEXT,
        scopes TEXT,
        rotated_ts TEXT
    );
    """,
    # 005 — notification platform (Phase 6.4)
    """
    CREATE TABLE IF NOT EXISTS notif_queue (
        id TEXT PRIMARY KEY,
        priority INTEGER NOT NULL,
        payload TEXT NOT NULL,
        dedup_key TEXT,
        not_before TEXT,
        expires_at TEXT,
        digest INTEGER NOT NULL DEFAULT 0,
        state TEXT NOT NULL,
        created_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_notif_queue_fetch 
        ON notif_queue(state, digest, not_before, expires_at, priority DESC, created_ts);

    CREATE TABLE IF NOT EXISTS notif_dead_letter (
        id TEXT PRIMARY KEY,
        reason TEXT NOT NULL,
        ts TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS notif_history (
        id TEXT PRIMARY KEY,
        correlation_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        priority INTEGER NOT NULL,
        channels TEXT NOT NULL,
        delivered INTEGER NOT NULL,
        final_provider TEXT,
        receipt TEXT NOT NULL,
        created_ts TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_notif_history_correlation 
        ON notif_history(correlation_id);
    """,
)


class Database:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._conn: aiosqlite.Connection | None = None

    async def start(self) -> None:
        if self._conn is not None:
            _log.warning("db.start_duplicate", event_type="db",
                         detail="closing existing connection before re-start")
            await self._conn.close()
            self._conn = None
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
        )
        await self._apply_migrations()
        await self._conn.commit()
        _log.info("db.ready", event_type="db", path=str(self._path), version=len(_MIGRATIONS))

    async def _apply_migrations(self) -> None:
        assert self._conn is not None
        cur = await self._conn.execute("SELECT version FROM schema_version LIMIT 1")
        row = await cur.fetchone()
        current = int(row["version"]) if row else 0
        for i, script in enumerate(_MIGRATIONS[current:], start=current + 1):
            await self._conn.executescript(script)
            _log.info("db.migrate", event_type="db", to_version=i)
        target = len(_MIGRATIONS)
        if row is None:
            await self._conn.execute("INSERT INTO schema_version(version) VALUES (?)", (target,))
        else:
            await self._conn.execute("UPDATE schema_version SET version=?", (target,))

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise FatalError("database not started")
        return self._conn

    async def stop(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def health(self) -> bool:
        return self._conn is not None
