"""Agent-run event reader — the live trace behind the run console (M2.2 Stage 2).

WHAT: a narrow, read-only seam that projects one run's durable event stream out of
the canonical ``events`` table. Every lifecycle turn (``agent.started`` ->
``agent.completed``/``limited``/``error``) and every tool turn (``tool.requested``
-> ``tool.completed``/``failed``) the engine emits is published through the bus and
persisted at publish time under ``causation_id = task_id = run_id`` (see
``infra.bus.MessageBus.publish``). So a run's full trace is already durable — this
reader just reads it back, ordered, with a resumable cursor.

WHY ``rowid`` as the sequence: the ``events`` table's primary key is ``id`` (a uuid
TEXT), so SQLite keeps a separate integer ``rowid`` that increases monotonically in
insert order. That is exactly the stable, gap-tolerant cursor an SSE stream needs
for ``id:`` frames and ``Last-Event-ID`` resume — no schema change, no new column.

WHY a Protocol + SQLite impl (mirrors ``persistence.AgentRunStore``): the reader is
a seam. The bus is SQLite-backed for the whole runtime, so ``SqliteAgentEventReader``
is the only live impl today; the Protocol keeps the SSE route decoupled from it and
lets tests inject a fake with no DB. This is a READ path only — it never writes, and
it adds no second execution funnel (Constitution): the events it reads were already
produced by the one governed loop.
"""

from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from atlas.infra.db import Database
from atlas.infra.logging import get_logger

_log = get_logger("atlas.agent_engine.event_reader")


class AgentRunEvent(BaseModel):
    """One persisted trace event for a run, projected for the live console.

    A faithful projection of an ``events`` row: ``sequence`` is the ``rowid`` cursor,
    ``type`` is the bus topic (``orchestrator`` | ``tool``), and ``payload`` is the
    full typed event dict — its ``kind`` (``agent.started``, ``tool.completed``, ...)
    and fields are what the console renders. Frozen: a delivered event never mutates.
    """

    model_config = {"frozen": True}
    sequence: int
    type: str
    correlation_id: str = ""
    causation_id: str | None = None
    occurred_at: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class AgentEventReader(Protocol):
    """Reads a run's durable event trace in order, from a resumable cursor.

    Async because the live impl is I/O-bound (aiosqlite); the SSE route depends only
    on this Protocol so the transport never touches the table directly.
    """

    async def events_for_run(
        self, run_id: str, *, after_sequence: int = 0, limit: int = 1000
    ) -> tuple[AgentRunEvent, ...]: ...


class SqliteAgentEventReader:
    """`AgentEventReader` over the shared `infra/db.py` `events` table.

    Reads every event whose ``causation_id`` is the run id, in ``rowid`` order,
    after a cursor. The bus persists on the same connection this reads from, so a
    committed event is immediately visible here — the stream never lags the loop.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    async def events_for_run(
        self, run_id: str, *, after_sequence: int = 0, limit: int = 1000
    ) -> tuple[AgentRunEvent, ...]:
        cur = await self._db.conn.execute(
            "SELECT rowid AS seq, type, correlation_id, causation_id, occurred_at, payload "
            "FROM events WHERE causation_id = ? AND rowid > ? ORDER BY rowid ASC LIMIT ?",
            (run_id, after_sequence, limit),
        )
        rows = await cur.fetchall()
        events: list[AgentRunEvent] = []
        for row in rows:
            try:
                payload = json.loads(row["payload"])
            except (TypeError, ValueError):
                # A row whose payload is not decodable JSON is a corrupt/foreign write;
                # surface it as an empty payload rather than aborting the whole stream.
                _log.warning("agent.event.bad_payload", event_type="db", run_id=run_id, seq=row["seq"])
                payload = {}
            events.append(
                AgentRunEvent(
                    sequence=row["seq"],
                    type=row["type"],
                    correlation_id=row["correlation_id"] or "",
                    causation_id=row["causation_id"],
                    occurred_at=row["occurred_at"] or "",
                    payload=payload,
                )
            )
        return tuple(events)
