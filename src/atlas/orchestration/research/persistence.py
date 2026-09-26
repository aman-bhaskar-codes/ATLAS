"""Research-session persistence (Phase 1, Slice R1) — durable, resumable research
sessions.

Mirrors ``orchestration/agent_engine/persistence.py`` exactly: a narrow
``ResearchSessionStore`` Protocol with two impls behind it — ``Sqlite...`` over the
shared ``infra/db.py`` substrate (one DB for the whole runtime — Constitution: one
persistence layer, no side doors), and a reserved ``Postgres...`` behind the SAME
protocol and SAME dialect-neutral SQL for a paid backend (Supabase/Neon), NOT
wired to a live client. Adopting Postgres is a constructor swap in the composition
root, not a store rewrite.

The FULL record is stored as JSON (``payload``) and reconstructed via
``model_validate_json`` so the result schema grows without a migration; the
indexed scalar columns are only what the session-list/filters query. The physical
table is ``research_query_sessions`` — deliberately distinct from the knowledge
fabric's internal ``research_sessions`` table (bounded-investigation budget state),
which is a different concept owned by a different layer.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from atlas.infra.backends import PostgresConnection
from atlas.infra.db import Database
from atlas.infra.logging import get_logger
from atlas.orchestration.research.records import ResearchSessionRecord

_log = get_logger("atlas.research.persistence")

_UPSERT = """
    INSERT INTO research_query_sessions
        (session_id, correlation_id, parent_session_id, question, mode, status, payload, created_ts, updated_ts)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(session_id) DO UPDATE SET
        correlation_id=excluded.correlation_id,
        parent_session_id=excluded.parent_session_id,
        question=excluded.question,
        mode=excluded.mode,
        status=excluded.status,
        payload=excluded.payload,
        updated_ts=excluded.updated_ts
"""


def _params(record: ResearchSessionRecord) -> tuple[object, ...]:
    """The upsert row for a record — shared by both backends (identical SQL)."""
    return (
        record.session_id,
        record.correlation_id,
        record.parent_session_id,
        record.question,
        record.mode,
        record.status,
        record.model_dump_json(),
        record.created_ts,
        record.updated_ts,
    )


@runtime_checkable
class ResearchSessionStore(Protocol):
    """Durable store for research sessions. Async because a remote (Postgres) impl
    is I/O-bound; the SQLite impl is too, via aiosqlite."""

    async def save_session(self, record: ResearchSessionRecord) -> None: ...

    async def load_session(self, session_id: str) -> ResearchSessionRecord | None: ...

    async def list_sessions(self, *, limit: int = 50) -> tuple[ResearchSessionRecord, ...]: ...

    async def delete_session(self, session_id: str) -> None: ...


class SqliteResearchSessionStore:
    """`ResearchSessionStore` over the shared `infra/db.py` SQLite substrate. Upserts
    are idempotent (`INSERT ... ON CONFLICT DO UPDATE`) so re-saving a session — the
    running-stub → terminal overwrite of Slice R3 — is safe and cheap."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def save_session(self, record: ResearchSessionRecord) -> None:
        await self._db.conn.execute(_UPSERT, _params(record))
        await self._db.conn.commit()
        _log.info("research.session.persisted", event_type="db", session_id=record.session_id, status=record.status)

    async def load_session(self, session_id: str) -> ResearchSessionRecord | None:
        cur = await self._db.conn.execute(
            "SELECT payload FROM research_query_sessions WHERE session_id=?", (session_id,)
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return ResearchSessionRecord.model_validate_json(row["payload"])

    async def list_sessions(self, *, limit: int = 50) -> tuple[ResearchSessionRecord, ...]:
        cur = await self._db.conn.execute(
            "SELECT payload FROM research_query_sessions ORDER BY updated_ts DESC LIMIT ?", (limit,)
        )
        rows = await cur.fetchall()
        return tuple(ResearchSessionRecord.model_validate_json(r["payload"]) for r in rows)

    async def delete_session(self, session_id: str) -> None:
        await self._db.conn.execute("DELETE FROM research_query_sessions WHERE session_id=?", (session_id,))
        await self._db.conn.commit()
        _log.info("research.session.deleted", event_type="db", session_id=session_id)


class PostgresResearchSessionStore:
    """`ResearchSessionStore` over a dedicated Postgres backend (Supabase/Neon). Same
    SQL as the SQLite impl — `ON CONFLICT` is identical and the `?` placeholders are
    translated to `$n` by `infra/backends.py`. Reserved: materialized behind the same
    protocol but NOT wired to a live client, so adopting Postgres is a constructor
    swap in the composition root, not a store rewrite."""

    def __init__(self, conn: PostgresConnection) -> None:
        self._conn = conn

    async def save_session(self, record: ResearchSessionRecord) -> None:
        await self._conn.execute(_UPSERT, _params(record))
        await self._conn.commit()
        _log.info(
            "research.session.persisted",
            event_type="db",
            backend="postgres",
            session_id=record.session_id,
            status=record.status,
        )

    async def load_session(self, session_id: str) -> ResearchSessionRecord | None:
        row = await self._conn.fetchone("SELECT payload FROM research_query_sessions WHERE session_id=?", (session_id,))
        if row is None:
            return None
        return ResearchSessionRecord.model_validate_json(row["payload"])

    async def list_sessions(self, *, limit: int = 50) -> tuple[ResearchSessionRecord, ...]:
        rows = await self._conn.fetchall(
            "SELECT payload FROM research_query_sessions ORDER BY updated_ts DESC LIMIT ?", (limit,)
        )
        return tuple(ResearchSessionRecord.model_validate_json(r["payload"]) for r in rows)

    async def delete_session(self, session_id: str) -> None:
        await self._conn.execute("DELETE FROM research_query_sessions WHERE session_id=?", (session_id,))
        await self._conn.commit()
        _log.info("research.session.deleted", event_type="db", backend="postgres", session_id=session_id)
