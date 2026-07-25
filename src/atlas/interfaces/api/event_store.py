"""Durable event store for the Live Run Console.

WHY separate from episodes: episodes are the episodic memory layer (Phase 3).
task_events is the structured, sequenced event log that the dashboard reads —
a distinct read model per the Phase Two spec and the Gap Audit.

WHY sequence here: the DB assigns monotonically increasing sequences per task,
so the client can detect gaps and request resync without trusting event arrival
order from the SSE stream.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from atlas.infra.db import Database
from atlas.interfaces.api.schemas import TaskEventResponse


@dataclass
class _EventRow:
    event_id: str
    task_id: str
    correlation_id: str
    sequence: int
    event_type: str
    state: str
    summary: str
    capability: str | None
    operation: str | None
    provider: str | None
    tier: int | None
    requires_approval: bool
    safe_metadata: dict[str, str]
    ts: str


class TaskEventStore:
    """Persists orchestrator events and provides query access for the API."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def record(
        self,
        *,
        task_id: str,
        correlation_id: str,
        event_type: str,
        state: str,
        summary: str,
        capability: str | None = None,
        operation: str | None = None,
        provider: str | None = None,
        tier: int | None = None,
        requires_approval: bool = False,
        safe_metadata: dict[str, Any] | None = None,
        ts: str,
    ) -> str:
        """Insert one event row; returns the assigned event_id."""
        event_id = str(uuid.uuid4())
        meta = {str(k): str(v) for k, v in (safe_metadata or {}).items()}
        # SQLite assigns the sequence via AUTOINCREMENT — we rely on the monotonic id
        # but expose a per-task sequence via a correlated subquery for robustness.
        await self._db.conn.execute(
            """
            INSERT INTO task_events
                (event_id, task_id, correlation_id, sequence, event_type, state,
                 summary, capability, operation, provider, tier, requires_approval,
                 safe_metadata, ts)
            VALUES (?, ?, ?,
                (SELECT COALESCE(MAX(sequence), 0) + 1 FROM task_events WHERE task_id = ?),
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id, task_id, correlation_id, task_id,
                event_type, state, summary,
                capability, operation, provider, tier,
                1 if requires_approval else 0,
                json.dumps(meta), ts,
            ),
        )
        await self._db.conn.commit()
        return event_id

    async def list_events(
        self,
        task_id: str,
        after_sequence: int | None = None,
        limit: int = 500,
    ) -> list[TaskEventResponse]:
        """Return ordered events for a task, optionally from a cursor sequence."""
        if after_sequence is not None:
            cur = await self._db.conn.execute(
                """SELECT event_id, task_id, correlation_id, sequence, event_type,
                          state, summary, capability, operation, provider, tier,
                          requires_approval, safe_metadata, ts
                   FROM task_events
                   WHERE task_id = ? AND sequence > ?
                   ORDER BY sequence ASC LIMIT ?""",
                (task_id, after_sequence, limit),
            )
        else:
            cur = await self._db.conn.execute(
                """SELECT event_id, task_id, correlation_id, sequence, event_type,
                          state, summary, capability, operation, provider, tier,
                          requires_approval, safe_metadata, ts
                   FROM task_events
                   WHERE task_id = ?
                   ORDER BY sequence ASC LIMIT ?""",
                (task_id, limit),
            )
        rows = await cur.fetchall()
        return [
            TaskEventResponse(
                event_id=r["event_id"],
                event_type=r["event_type"],
                ts=r["ts"],
                task_id=r["task_id"],
                correlation_id=r["correlation_id"],
                execution_id=None,
                sequence=r["sequence"],
                state=r["state"],
                summary=r["summary"],
                capability=r["capability"],
                operation=r["operation"],
                provider=r["provider"],
                tier=r["tier"],
                requires_approval=bool(r["requires_approval"]),
                safe_metadata=json.loads(r["safe_metadata"] or "{}"),
            )
            for r in rows
        ]
