"""SQLite implementations of the execution persistence contracts.

Satisfies `atlas.orchestration.stores.ExecutionStore` /
`CancellationStore` structurally (by signature). This module deliberately does
NOT import orchestration — infra knows mechanics, not policy — which is exactly
what the import-linter contracts require.

Cancellation persistence uses the existing `tasks.state` column rather than a
new table: 'cancelling' is already a legal task state, so there is one source
of truth and no migration. The orchestrator overwrites the state with the
terminal value (FAILED) in its finally block, which implicitly clears intent.
"""

from __future__ import annotations

from datetime import datetime

from atlas.infra.db import Database
from atlas.infra.backends import SQLiteConnection
from atlas.infra.execution_lease import OWNER_GUARD, ExecutionLeaseStore, OwnershipLostError, current_execution_owner


class SQLiteExecutionStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    @property
    def database(self) -> Database:
        return self._db

    @property
    def leases(self) -> ExecutionLeaseStore:
        return ExecutionLeaseStore(SQLiteConnection(self._db.conn))

    async def create_task(
        self,
        *,
        task_id: str,
        source: str,
        payload_json: str,
        idempotency_key: str | None,
        created_ts: datetime,
    ) -> None:
        owner = current_execution_owner()
        sql = (
            "INSERT OR IGNORE INTO tasks(id, source, state, payload, "
            "idempotency_key, created_ts, updated_ts) "
        )
        params = (
                task_id,
                source,
                "created",
                payload_json,
                idempotency_key,
                created_ts.isoformat(),
                created_ts.isoformat(),
            )
        if owner is not None:
            await self._db.conn.execute(sql + "SELECT ?,?,?,?,?,?,? WHERE " + OWNER_GUARD,
                                        (*params, *owner.parameters()))
            row = await (await self._db.conn.execute("SELECT state FROM tasks WHERE id=?", (task_id,))).fetchone()
            if row is None or row["state"] != "created":
                raise OwnershipLostError("task was already started or is terminal")
        else:
            await self._db.conn.execute(sql + "VALUES (?,?,?,?,?,?,?)", params)
        await self._db.conn.commit()

    async def update_task_state(self, *, task_id: str, state: str, updated_ts: datetime) -> None:
        owner = current_execution_owner()
        sql = "UPDATE tasks SET state=?, updated_ts=? WHERE id=?"
        params: tuple[object, ...] = (state, updated_ts.isoformat(), task_id)
        if owner is not None:
            sql += " AND " + OWNER_GUARD
            params += owner.parameters()
        cur = await self._db.conn.execute(sql, params)
        if owner is not None and cur.rowcount != 1:
            raise OwnershipLostError("task lease expired or changed owner")
        await self._db.conn.commit()

    async def fail_task_if_running(self, task_id: str, updated_ts: datetime) -> None:
        owner = current_execution_owner()
        if owner is None:
            return
        await self._db.conn.execute(
            "UPDATE tasks SET state='failed',updated_ts=? WHERE id=? "
            "AND state NOT IN ('failed','completed','archived') AND " + OWNER_GUARD,
            (updated_ts.isoformat(), task_id, *owner.parameters()),
        )
        await self._db.conn.commit()


class SQLiteCancellationStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def request_cancellation(self, task_id: str) -> bool:
        cur = await self._db.conn.execute(
            "UPDATE tasks SET state='cancelling', "
            "updated_ts=updated_ts WHERE id=? AND state NOT IN "
            "('failed','completed','archived','cancelling')",
            (task_id,),
        )
        await self._db.conn.commit()
        return cur.rowcount > 0

    async def is_cancelled(self, task_id: str) -> bool:
        cur = await self._db.conn.execute("SELECT state FROM tasks WHERE id = ?", (task_id,))
        row = await cur.fetchone()
        return row is not None and row["state"] == "cancelling"

    async def clear(self, task_id: str) -> None:
        # Terminal state writes supersede 'cancelling'; nothing to delete.
        return None
