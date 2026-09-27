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

import json
from typing import Any, Protocol, runtime_checkable

from atlas.infra.backends import PostgresConnection
from atlas.infra.db import Database
from atlas.infra.logging import get_logger
from atlas.orchestration.research.records import ResearchEvent, ResearchSessionRecord

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


# ── Event trace (Slice R3) ───────────────────────────────────────────── #
_INSERT_EVENT = "INSERT INTO research_query_events (session_id, phase, payload, ts) VALUES (?, ?, ?, ?)"
_SELECT_EVENTS = (
    "SELECT sequence, session_id, phase, payload, ts FROM research_query_events "
    "WHERE session_id = ? AND sequence > ? ORDER BY sequence ASC LIMIT ?"
)


def _event_from_row(row: Any) -> ResearchEvent:
    """Project a ``research_query_events`` row into a typed ``ResearchEvent``.

    A payload that is not decodable JSON (a corrupt/foreign write) degrades to an
    empty dict rather than aborting the whole stream — same tolerance the agent
    event reader applies.
    """
    try:
        payload = json.loads(row["payload"])
    except (TypeError, ValueError):
        payload = {}
    return ResearchEvent(
        sequence=int(row["sequence"]),
        session_id=row["session_id"] or "",
        phase=row["phase"] or "",
        payload=payload if isinstance(payload, dict) else {},
        ts=row["ts"] or "",
    )


@runtime_checkable
class ResearchSessionStore(Protocol):
    """Durable store for research sessions. Async because a remote (Postgres) impl
    is I/O-bound; the SQLite impl is too, via aiosqlite."""

    async def save_session(self, record: ResearchSessionRecord) -> None: ...

    async def load_session(self, session_id: str) -> ResearchSessionRecord | None: ...

    async def list_sessions(self, *, limit: int = 50) -> tuple[ResearchSessionRecord, ...]: ...

    async def delete_session(self, session_id: str) -> None: ...

    async def append_event(self, session_id: str, phase: str, payload: dict[str, Any], *, ts: str) -> ResearchEvent: ...

    async def list_events(self, session_id: str, *, after_sequence: int = 0, limit: int = 1000) -> tuple[ResearchEvent, ...]: ...


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

    async def append_event(self, session_id: str, phase: str, payload: dict[str, Any], *, ts: str) -> ResearchEvent:
        cur = await self._db.conn.execute(_INSERT_EVENT, (session_id, phase, json.dumps(payload), ts))
        await self._db.conn.commit()
        # AUTOINCREMENT sequence assigned by SQLite — the SSE cursor / Last-Event-ID key.
        return ResearchEvent(sequence=int(cur.lastrowid or 0), session_id=session_id, phase=phase, payload=payload, ts=ts)

    async def list_events(
        self, session_id: str, *, after_sequence: int = 0, limit: int = 1000
    ) -> tuple[ResearchEvent, ...]:
        cur = await self._db.conn.execute(_SELECT_EVENTS, (session_id, after_sequence, limit))
        rows = await cur.fetchall()
        return tuple(_event_from_row(r) for r in rows)


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

    async def append_event(self, session_id: str, phase: str, payload: dict[str, Any], *, ts: str) -> ResearchEvent:
        # RETURNING the serial sequence keeps the SSE cursor authoritative on the remote
        # backend too — same dialect-neutral shape, `?` translated to `$n` by infra/backends.
        row = await self._conn.fetchone(
            _INSERT_EVENT + " RETURNING sequence", (session_id, phase, json.dumps(payload), ts)
        )
        sequence = int(row["sequence"]) if row is not None else 0
        await self._conn.commit()
        return ResearchEvent(sequence=sequence, session_id=session_id, phase=phase, payload=payload, ts=ts)

    async def list_events(
        self, session_id: str, *, after_sequence: int = 0, limit: int = 1000
    ) -> tuple[ResearchEvent, ...]:
        rows = await self._conn.fetchall(_SELECT_EVENTS, (session_id, after_sequence, limit))
        return tuple(_event_from_row(r) for r in rows)
