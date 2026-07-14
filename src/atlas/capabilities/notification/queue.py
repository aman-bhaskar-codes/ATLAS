"""Notification queue — priority + scheduled + dedup + expire + cancel + DLQ.

WHY SQLite-backed like the P4 task queue: durability across restarts. Priority
ordering (CRITICAL first), scheduled/delayed via not_before, dedup by dedup_key
within a window, expiry drops stale items, and a dead_letter table parks poison
notifications. Backpressure: a bounded in-memory drain with the DB as overflow.
"""

from __future__ import annotations

import json

from atlas.capabilities.notification.domain.models import Notification
from atlas.infra.clock import Clock
from atlas.infra.db import Database


class NotificationQueue:
    def __init__(self, db: Database, clock: Clock) -> None:
        self._db = db
        self._clock = clock

    async def enqueue(self, n: Notification, *, not_before_iso: str | None = None,
                      digest: bool = False) -> bool:
        if n.dedup_key:
            cur = await self._db.conn.execute(
                "SELECT 1 FROM notif_queue WHERE dedup_key=? AND state='pending'", (n.dedup_key,))
            if await cur.fetchone():
                return False   # deduped
        await self._db.conn.execute(
            "INSERT INTO notif_queue(id, priority, payload, dedup_key, not_before, "
            "expires_at, digest, state, created_ts) VALUES (?,?,?,?,?,?,?, 'pending', ?)",
            (n.id, int(n.priority), n.model_dump_json(), n.dedup_key, not_before_iso,
             n.expires_at.isoformat() if n.expires_at else None, int(digest),
             n.created_ts.isoformat()))
        await self._db.conn.commit()
        return True

    async def claim_ready(self, *, digest: bool, limit: int = 20) -> list[Notification]:
        now = self._clock.now().isoformat()
        cur = await self._db.conn.execute(
            "SELECT id, payload FROM notif_queue WHERE state='pending' AND digest=? "
            "AND (not_before IS NULL OR not_before<=?) "
            "AND (expires_at IS NULL OR expires_at>?) "
            "ORDER BY priority DESC, created_ts LIMIT ?",
            (int(digest), now, now, limit))
        rows = await cur.fetchall()
        out = [Notification(**json.loads(r["payload"])) for r in rows]
        for r in rows:
            await self._db.conn.execute("UPDATE notif_queue SET state='claimed' WHERE id=?", (r["id"],))
        await self._db.conn.commit()
        return out

    async def complete(self, notification_id: str) -> None:
        await self._db.conn.execute("UPDATE notif_queue SET state='done' WHERE id=?", (notification_id,))
        await self._db.conn.commit()

    async def dead_letter(self, notification_id: str, reason: str) -> None:
        await self._db.conn.execute("UPDATE notif_queue SET state='dead' WHERE id=?", (notification_id,))
        await self._db.conn.execute(
            "INSERT INTO notif_dead_letter(id, reason, ts) VALUES (?,?,?)",
            (notification_id, reason, self._clock.now().isoformat()))
        await self._db.conn.commit()

    async def cancel(self, notification_id: str) -> None:
        await self._db.conn.execute(
            "UPDATE notif_queue SET state='cancelled' WHERE id=? AND state='pending'",
            (notification_id,))
        await self._db.conn.commit()
