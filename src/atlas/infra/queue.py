"""Durable task queue — at-least-once delivery with atomic claims.

Claiming is one conditional UPDATE per row (state pending -> claimed, only
when not already claimed or claimed-lease expired). That is atomic under
SQLite WAL's single writer AND PostgreSQL row locks, so N worker processes
can consume the same table without double-claiming. Attempts are capped;
exhausted jobs move to 'dead' (visible, never silently dropped).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from atlas.infra.backends import Connection

# A claim older than this is considered crashed and can be re-claimed.
CLAIM_LEASE_S = 600.0


@dataclass(frozen=True)
class QueueJob:
    id: int
    payload: dict[str, Any]
    tenant_id: str
    attempts: int
    max_attempts: int


class DurableTaskQueue:
    def __init__(self, conn: Connection, worker_id: str) -> None:
        self._conn = conn
        self._worker = worker_id

    async def enqueue(self, payload: dict[str, Any], *, tenant_id: str = "local", max_attempts: int = 3) -> int:
        cur = await self._conn.fetchone("SELECT MAX(id) AS m FROM task_queue")
        next_id = (cur["m"] or 0) + 1 if cur else 1
        await self._conn.execute(
            "INSERT INTO task_queue (id, payload, tenant_id, state, max_attempts, created_ts) "
            "VALUES (?,?,?,'pending',?,?)",
            (next_id, json.dumps(payload), tenant_id, max_attempts, datetime.now(UTC).isoformat()),
        )
        await self._conn.commit()
        return next_id

    async def claim(self) -> QueueJob | None:
        """Atomically claim the oldest pending (or lease-expired) job."""
        lease_cutoff = (datetime.now(UTC) - timedelta(seconds=CLAIM_LEASE_S)).isoformat()
        row = await self._conn.fetchone(
            "SELECT * FROM task_queue WHERE state = 'pending' "
            "OR (state = 'claimed' AND claimed_ts < ?) "
            "ORDER BY created_ts ASC, id ASC LIMIT 1",
            (lease_cutoff,),
        )
        if row is None:
            return None
        now = datetime.now(UTC).isoformat()
        claimed = await self._conn.execute(
            "UPDATE task_queue SET state = 'claimed', claimed_by = ?, claimed_ts = ?, "
            "attempts = attempts + 1 WHERE id = ? AND (state = 'pending' "
            "OR (state = 'claimed' AND claimed_ts < ?))",
            (self._worker, now, row["id"], lease_cutoff),
        )
        await self._conn.commit()
        if claimed == 0:
            return None  # lost the race; another worker got it
        return QueueJob(
            id=row["id"],
            payload=json.loads(row["payload"]),
            tenant_id=row["tenant_id"],
            attempts=row["attempts"] + 1,
            max_attempts=row["max_attempts"],
        )

    async def complete(self, job_id: int) -> None:
        await self._conn.execute(
            "UPDATE task_queue SET state = 'done', completed_ts = ? WHERE id = ?",
            (datetime.now(UTC).isoformat(), job_id),
        )
        await self._conn.commit()

    async def fail(self, job_id: int, *, attempts: int, max_attempts: int) -> str:
        """Record a failure. Returns the new state ('pending' retry or 'dead')."""
        state = "pending" if attempts < max_attempts else "dead"
        await self._conn.execute("UPDATE task_queue SET state = ? WHERE id = ?", (state, job_id))
        await self._conn.commit()
        return state

    async def stats(self) -> dict[str, int]:
        rows = await self._conn.fetchall("SELECT state, COUNT(*) AS n FROM task_queue GROUP BY state")
        return {r["state"]: r["n"] for r in rows}
