"""Agent-run persistence — durable, resumable native tool-calling runs (M0.5).

WHAT: a narrow store so a run minted by ``AgentEngine.run(...)`` in one process is
addressable and CONTINUABLE in the next. It persists the ``AgentRunRecord``
envelope (seed conversation + result trace); the full conversation is rebuilt on
load via ``agent_engine.context.reconstruct_conversation``, so the engine loop
never has to know it is being persisted.

WHY a Protocol + two impls (mirrors ``capabilities/ide/persistence.py``):
``AgentRunStore`` is the seam. ``SqliteAgentRunStore`` runs over the shared
``infra/db.py`` substrate (one DB for the whole runtime — Constitution: one
persistence layer, no side doors). ``PostgresAgentRunStore`` is the reserved
second impl behind the SAME protocol for a paid backend (Supabase/Neon); it is
materialized but NOT wired to a live client — swapping it is a constructor change
in the composition root, not a store rewrite. The migration DDL (``agent_runs``)
is deliberately dialect-neutral for exactly that port.

The FULL record is stored as JSON (``payload``) and reconstructed via
``model_validate_json``, so the trace schema grows with the engine without a
migration; the indexed scalar columns are only what the run-list/filters query.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from atlas.infra.backends import PostgresConnection
from atlas.infra.db import Database
from atlas.infra.logging import get_logger
from atlas.orchestration.agent_engine.records import AgentRunRecord

_log = get_logger("atlas.agent_engine.persistence")

_UPSERT = """
    INSERT INTO agent_runs
        (run_id, task_id, correlation_id, workspace_id, session_id, request, status, payload, created_ts, updated_ts)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(run_id) DO UPDATE SET
        task_id=excluded.task_id,
        correlation_id=excluded.correlation_id,
        workspace_id=excluded.workspace_id,
        session_id=excluded.session_id,
        request=excluded.request,
        status=excluded.status,
        payload=excluded.payload,
        updated_ts=excluded.updated_ts
"""


def _params(record: AgentRunRecord) -> tuple[object, ...]:
    """The upsert row for a record — shared by both backends (identical SQL)."""
    return (
        record.run_id,
        record.task_id,
        record.correlation_id,
        record.workspace_id,
        record.session_id,
        record.request,
        record.status,
        record.model_dump_json(),
        record.created_ts,
        record.updated_ts,
    )


@runtime_checkable
class AgentRunStore(Protocol):
    """Durable store for agent-engine runs. Async because a remote (Postgres) impl
    is I/O-bound; the SQLite impl is too, via aiosqlite."""

    async def save_run(self, record: AgentRunRecord) -> None: ...

    async def load_run(self, run_id: str) -> AgentRunRecord | None: ...

    async def list_runs(self, *, workspace_id: str | None = None, limit: int = 50) -> tuple[AgentRunRecord, ...]: ...

    async def runs_for_session(self, session_id: str) -> tuple[AgentRunRecord, ...]: ...

    async def delete_run(self, run_id: str) -> None: ...


class SqliteAgentRunStore:
    """`AgentRunStore` over the shared `infra/db.py` SQLite substrate. Upserts are
    idempotent (`INSERT ... ON CONFLICT DO UPDATE`) so re-saving a run after every
    step — the durable-checkpoint pattern — is safe and cheap."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def save_run(self, record: AgentRunRecord) -> None:
        await self._db.conn.execute(_UPSERT, _params(record))
        await self._db.conn.commit()
        _log.info("agent.run.persisted", event_type="db", run_id=record.run_id, status=record.status)

    async def load_run(self, run_id: str) -> AgentRunRecord | None:
        cur = await self._db.conn.execute("SELECT payload FROM agent_runs WHERE run_id=?", (run_id,))
        row = await cur.fetchone()
        if row is None:
            return None
        return AgentRunRecord.model_validate_json(row["payload"])

    async def list_runs(self, *, workspace_id: str | None = None, limit: int = 50) -> tuple[AgentRunRecord, ...]:
        if workspace_id is not None:
            cur = await self._db.conn.execute(
                "SELECT payload FROM agent_runs WHERE workspace_id=? ORDER BY updated_ts DESC LIMIT ?",
                (workspace_id, limit),
            )
        else:
            cur = await self._db.conn.execute(
                "SELECT payload FROM agent_runs ORDER BY updated_ts DESC LIMIT ?", (limit,)
            )
        rows = await cur.fetchall()
        return tuple(AgentRunRecord.model_validate_json(r["payload"]) for r in rows)

    async def runs_for_session(self, session_id: str) -> tuple[AgentRunRecord, ...]:
        cur = await self._db.conn.execute(
            "SELECT payload FROM agent_runs WHERE session_id=? ORDER BY updated_ts DESC", (session_id,)
        )
        rows = await cur.fetchall()
        return tuple(AgentRunRecord.model_validate_json(r["payload"]) for r in rows)

    async def delete_run(self, run_id: str) -> None:
        await self._db.conn.execute("DELETE FROM agent_runs WHERE run_id=?", (run_id,))
        await self._db.conn.commit()
        _log.info("agent.run.deleted", event_type="db", run_id=run_id)


class PostgresAgentRunStore:
    """`AgentRunStore` over a dedicated Postgres backend (Supabase/Neon). Same SQL
    as the SQLite impl — `ON CONFLICT` is identical and the `?` placeholders are
    translated to `$n` by `infra/backends.py`. Reserved: materialized behind the
    same protocol but NOT wired to a live client, so adopting Postgres is a
    constructor swap in the composition root, not a store rewrite."""

    def __init__(self, conn: PostgresConnection) -> None:
        self._conn = conn

    async def save_run(self, record: AgentRunRecord) -> None:
        await self._conn.execute(_UPSERT, _params(record))
        await self._conn.commit()
        _log.info(
            "agent.run.persisted", event_type="db", backend="postgres", run_id=record.run_id, status=record.status
        )

    async def load_run(self, run_id: str) -> AgentRunRecord | None:
        row = await self._conn.fetchone("SELECT payload FROM agent_runs WHERE run_id=?", (run_id,))
        if row is None:
            return None
        return AgentRunRecord.model_validate_json(row["payload"])

    async def list_runs(self, *, workspace_id: str | None = None, limit: int = 50) -> tuple[AgentRunRecord, ...]:
        if workspace_id is not None:
            rows = await self._conn.fetchall(
                "SELECT payload FROM agent_runs WHERE workspace_id=? ORDER BY updated_ts DESC LIMIT ?",
                (workspace_id, limit),
            )
        else:
            rows = await self._conn.fetchall(
                "SELECT payload FROM agent_runs ORDER BY updated_ts DESC LIMIT ?", (limit,)
            )
        return tuple(AgentRunRecord.model_validate_json(r["payload"]) for r in rows)

    async def runs_for_session(self, session_id: str) -> tuple[AgentRunRecord, ...]:
        rows = await self._conn.fetchall(
            "SELECT payload FROM agent_runs WHERE session_id=? ORDER BY updated_ts DESC", (session_id,)
        )
        return tuple(AgentRunRecord.model_validate_json(r["payload"]) for r in rows)

    async def delete_run(self, run_id: str) -> None:
        await self._conn.execute("DELETE FROM agent_runs WHERE run_id=?", (run_id,))
        await self._conn.commit()
        _log.info("agent.run.deleted", event_type="db", backend="postgres", run_id=run_id)
