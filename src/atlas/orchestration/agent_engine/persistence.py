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

from atlas.infra.backends import PostgresConnection, SQLiteConnection
from atlas.infra.db import Database
from atlas.infra.execution_lease import (
    ORPHAN_GUARD,
    POSTGRES_ORPHAN_CTE,
    POSTGRES_ORPHAN_GUARD,
    POSTGRES_OWNER_GUARD,
    ExecutionLeaseStore,
    OwnershipLostError,
    guarded_upsert,
)
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

    async def running_runs(self) -> tuple[AgentRunRecord, ...]: ...

    @property
    def leases(self) -> ExecutionLeaseStore: ...

    async def save_owned(self, record: AgentRunRecord, owner_id: str, *, now: str) -> None: ...

    async def settle_orphan(self, before: AgentRunRecord, after: AgentRunRecord, *, now: str) -> bool: ...


class SqliteAgentRunStore:
    """`AgentRunStore` over the shared `infra/db.py` SQLite substrate. Upserts are
    idempotent (`INSERT ... ON CONFLICT DO UPDATE`) so re-saving a run after every
    step — the durable-checkpoint pattern — is safe and cheap."""

    def __init__(self, db: Database) -> None:
        self._db = db

    @property
    def leases(self) -> ExecutionLeaseStore:
        return ExecutionLeaseStore(SQLiteConnection(self._db.conn))

    async def save_owned(self, record: AgentRunRecord, owner_id: str, *, now: str) -> None:
        guard = ("agent:" + record.run_id, owner_id, now)
        cur = await self._db.conn.execute(guarded_upsert(_UPSERT), (*_params(record), *guard, *guard))
        await self._db.conn.commit()
        if cur.rowcount != 1:
            raise OwnershipLostError(f"agent run {record.run_id!r} no longer owned")

    async def settle_orphan(self, before: AgentRunRecord, after: AgentRunRecord, *, now: str) -> bool:
        cur = await self._db.conn.execute("SELECT payload FROM agent_runs WHERE run_id=?", (before.run_id,))
        row = await cur.fetchone()
        if row is None or AgentRunRecord.model_validate_json(row["payload"]) != before:
            return False
        cur = await self._db.conn.execute(
            "UPDATE agent_runs SET status=?,payload=?,updated_ts=? WHERE run_id=? AND status='running' "
            "AND payload=? AND " + ORPHAN_GUARD,
            (
                after.status,
                after.model_dump_json(),
                after.updated_ts,
                before.run_id,
                row["payload"],
                "agent:" + before.run_id,
                now,
            ),
        )
        await self._db.conn.commit()
        return cur.rowcount == 1

    async def running_runs(self) -> tuple[AgentRunRecord, ...]:
        cur = await self._db.conn.execute("SELECT payload FROM agent_runs WHERE status='running'")
        return tuple(AgentRunRecord.model_validate_json(row["payload"]) for row in await cur.fetchall())

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

    @property
    def leases(self) -> ExecutionLeaseStore:
        return ExecutionLeaseStore(self._conn)

    async def save_owned(self, record: AgentRunRecord, owner_id: str, *, now: str) -> None:
        guard = ("agent:" + record.run_id, owner_id, now)
        changed = await self._conn.execute(
            guarded_upsert(_UPSERT, owner_guard=POSTGRES_OWNER_GUARD), (*_params(record), *guard, *guard)
        )
        await self._conn.commit()
        if changed != 1:
            raise OwnershipLostError(f"agent run {record.run_id!r} no longer owned")

    async def settle_orphan(self, before: AgentRunRecord, after: AgentRunRecord, *, now: str) -> bool:
        row = await self._conn.fetchone("SELECT payload FROM agent_runs WHERE run_id=?", (before.run_id,))
        if row is None or AgentRunRecord.model_validate_json(row["payload"]) != before:
            return False
        changed = await self._conn.execute(
            POSTGRES_ORPHAN_CTE
            + "UPDATE agent_runs SET status=?,payload=?,updated_ts=? WHERE run_id=? AND status='running' "
            "AND payload=? AND " + POSTGRES_ORPHAN_GUARD,
            (
                "agent:" + before.run_id,
                after.status,
                after.model_dump_json(),
                after.updated_ts,
                before.run_id,
                row["payload"],
                now,
            ),
        )
        await self._conn.commit()
        return changed == 1

    async def running_runs(self) -> tuple[AgentRunRecord, ...]:
        rows = await self._conn.fetchall("SELECT payload FROM agent_runs WHERE status='running'")
        return tuple(AgentRunRecord.model_validate_json(row["payload"]) for row in rows)

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
