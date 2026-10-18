"""Durable event store for the Live Run Console.

WHY separate from episodes: episodes are the episodic memory layer (Phase 3).
task_events is the structured, sequenced event log that the dashboard reads —
a distinct read model per the Phase Two spec and the Gap Audit.

WHY sequence here: the DB assigns monotonically increasing sequences per task,
so the client can detect gaps and request resync without trusting event arrival
order from the SSE stream.
"""

from __future__ import annotations

import bisect
from collections import OrderedDict, deque
import itertools
import json
import uuid
from dataclasses import dataclass
from typing import Any

from atlas.infra.db import Database
from atlas.interfaces.api.schemas import TaskEventResponse

_MAX_RING_BUFFER_PER_TASK = 500
_MAX_TRACKED_TASKS = 1000


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
    """Persists orchestrator events and provides query access for the API.

    Backed by:
    1. An in-memory Bounded Circular Ring Buffer (deque with maxlen=500) per task
       for O(1) ingestion and O(log N) bisect_right cursor replay.
    2. SQLite task_events table for durable long-term storage and full historical
       recovery.
    """

    def __init__(self, db: Database) -> None:
        self._db = db
        self._ring_buffers: OrderedDict[str, deque[TaskEventResponse]] = OrderedDict()

    def _get_or_create_buffer(self, task_id: str) -> deque[TaskEventResponse]:
        if task_id in self._ring_buffers:
            self._ring_buffers.move_to_end(task_id)
            return self._ring_buffers[task_id]
        if len(self._ring_buffers) >= _MAX_TRACKED_TASKS:
            self._ring_buffers.popitem(last=False)
        buf: deque[TaskEventResponse] = deque(maxlen=_MAX_RING_BUFFER_PER_TASK)
        self._ring_buffers[task_id] = buf
        return buf

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
        buf = self._get_or_create_buffer(task_id)
        if len(buf) > 0:
            assigned_seq = buf[-1].sequence + 1
        else:
            cur = await self._db.conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM task_events WHERE task_id = ?",
                (task_id,),
            )
            row = await cur.fetchone()
            assigned_seq = int(row[0]) if row else 1
            await cur.close()

        await self._db.conn.execute(
            """
            INSERT INTO task_events
                (event_id, task_id, correlation_id, sequence, event_type, state,
                 summary, capability, operation, provider, tier, requires_approval,
                 safe_metadata, ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                task_id,
                correlation_id,
                assigned_seq,
                event_type,
                state,
                summary,
                capability,
                operation,
                provider,
                tier,
                1 if requires_approval else 0,
                json.dumps(meta),
                ts,
            ),
        )
        await self._db.conn.commit()

        # Update in-memory ring buffer
        event_resp = TaskEventResponse(
            event_id=event_id,
            event_type=event_type,
            ts=ts,
            task_id=task_id,
            correlation_id=correlation_id,
            execution_id=None,
            sequence=assigned_seq,
            state=state,
            summary=summary,
            capability=capability,
            operation=operation,
            provider=provider,
            tier=tier,
            requires_approval=requires_approval,
            safe_metadata=meta,
        )
        buf = self._get_or_create_buffer(task_id)
        buf.append(event_resp)

        return event_id

    async def list_events(
        self,
        task_id: str,
        after_sequence: int | None = None,
        limit: int = 500,
    ) -> list[TaskEventResponse]:
        """Return ordered events for a task, optionally from a cursor sequence.

        Fast path: O(log N) bisect_right search in the in-memory ring buffer when
        all requested events are within memory bounds.
        Slow path: Falls back to indexed SQLite query when buffer has wrapped or is empty.
        """
        buf = self._ring_buffers.get(task_id)
        if buf and len(buf) > 0 and after_sequence is not None:
            # Check if all events newer than after_sequence are retained in the buffer
            oldest_seq = buf[0].sequence
            if oldest_seq <= after_sequence + 1:
                seqs = [e.sequence for e in buf]
                idx = bisect.bisect_right(seqs, after_sequence)
                return list(itertools.islice(buf, idx, idx + limit))

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
        await cur.close()
        results = [
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
        # Prime the ring buffer if it was empty
        if not buf and results:
            target_buf = self._get_or_create_buffer(task_id)
            for res in results[-_MAX_RING_BUFFER_PER_TASK:]:
                target_buf.append(res)

        return results
