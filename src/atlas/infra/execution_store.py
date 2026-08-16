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


class SQLiteExecutionStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create_task(
        self,
        *,
        task_id: str,
        source: str,
        payload_json: str,
        idempotency_key: str | None,
        created_ts: datetime,
    ) -> None:
        await self._db.conn.execute(
            "INSERT OR IGNORE INTO tasks(id, source, state, payload, "
            "idempotency_key, created_ts, updated_ts) VALUES (?,?,?,?,?,?,?)",
            (
                task_id,
                source,
                "created",
                payload_json,
                idempotency_key,
                created_ts.isoformat(),
                created_ts.isoformat(),
            ),
        )
        await self._db.conn.commit()

    async def update_task_state(self, *, task_id: str, state: str, updated_ts: datetime) -> None:
        await self._db.conn.execute(
            "UPDATE tasks SET state=?, updated_ts=? WHERE id=?",
            (state, updated_ts.isoformat(), task_id),
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
