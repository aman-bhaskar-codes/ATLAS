"""Typed message bus + event taxonomy.

WHY concurrent-with-isolation delivery: handlers should run in parallel, but one
handler raising must not stop the others or the publisher. WHY typed Event base:
no stringly-typed payloads cross the bus.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, cast

from aiosqlite import Row
from pydantic import BaseModel, Field

from atlas.autonomy.events import AtlasEvent, DeliveryStatus, DurabilityTier
from atlas.infra.db import Database
from atlas.infra.errors import BusError
from atlas.infra.logging import get_logger

_log = get_logger("atlas.bus")


class Event(BaseModel):
    """Base for all bus events. Subclasses add typed fields."""

    model_config = {"frozen": True}
    correlation_id: str


# ---------------------------------------------------------------------------
# Memory events — defined here (infra layer) so atlas.memory can import them
# without violating the layer boundary. atlas.orchestration.events re-exports.
# ---------------------------------------------------------------------------


class MemoryBusEvent(Event):
    """Published to the 'memory' topic on any memory write or retrieval.

    WHY in infra.bus: atlas.memory cannot import atlas.orchestration (layer
    boundary). Defining the event here lets memory modules publish typed events
    while importing only from atlas.infra.
    """

    task_id: str
    kind: str  # memory.stored | memory.retrieved | memory.consolidated | ...
    memory_type: str  # episodic | semantic | working | user_model | knowledge
    count: int = 0
    query: str | None = None
    items: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


Handler = Callable[[Event], Awaitable[None]]


class Topic:
    SYSTEM_STARTUP = "system.startup"
    SYSTEM_SHUTDOWN = "system.shutdown"
    TASK_CREATED = "task.created"
    SAFETY_CLASSIFY = "safety.classify"
    SAFETY_DECISION = "safety.decision"
    SAFETY_CONFIRM_REQUESTED = "safety.confirm.requested"
    SAFETY_CONFIRM_RESOLVED = "safety.confirm.resolved"
    CONTROL_KILL = "control.kill"
    MODEL_ROUTE = "model.route"
    MODEL_CALL = "model.call"


class MessageBus:
    def __init__(self, db: Database) -> None:
        self._db = db
        self._subs: dict[str, list[Handler]] = defaultdict(list)
        self._global_subs: list[Callable[[str, str], Awaitable[None]]] = []
        self._closed = False
        self._wake_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._event_types: dict[str, type[Event]] = {}

    def register_type(self, topic: str, event_cls: type[Event]) -> None:
        self._event_types[topic] = event_cls

    async def start(self) -> None:
        if self._task is None:
            # Recover any events stuck in_flight from a previous crash
            await self.recover_in_flight()
            self._task = asyncio.create_task(self._process_queue())

    def subscribe(self, topic: str, handler: Handler) -> None:
        self._subs[topic].append(handler)

    def subscribe_global(self, handler: Callable[[str, str], Awaitable[None]]) -> None:
        """Subscribe to all events (topic, payload_json) independent of specific typing."""
        self._global_subs.append(handler)

    async def publish(self, topic: str, event: Event) -> None:
        if self._closed:
            raise BusError("publish on a closed bus")

        now = datetime.now(UTC).isoformat()

        event_dict = event.model_dump()
        task_id = event_dict.get("task_id")
        correlation_id = event_dict.get("correlation_id", getattr(event, "correlation_id", "unknown"))

        # Check idempotency: skip if an event with the same key was already delivered
        dedup_key = getattr(event, "deduplication_key", None)
        if dedup_key is not None:
            cur = await self._db.conn.execute(
                "SELECT delivery_status FROM events WHERE deduplication_key = ? ORDER BY occurred_at ASC LIMIT 1",
                (dedup_key,),
            )
            existing = await cur.fetchone()
            if existing and existing["delivery_status"] == "delivered":
                _log.debug("bus.publish.idempotent_skip", event_type="bus", dedup_key=dedup_key)
                return

        atlas_event = AtlasEvent(
            id=str(uuid.uuid4()),
            type=topic,
            source="system",
            correlation_id=correlation_id,
            causation_id=task_id,
            occurred_at=now,
            payload=event_dict,
            metadata={"original_type": event.__class__.__name__},
        )

        payload_json = event.model_dump_json()

        await self._db.conn.execute(
            """INSERT INTO events(
                id, type, source, correlation_id, causation_id, deduplication_key,
                occurred_at, payload, metadata, schema_version, durability,
                delivery_status, attempt_count
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                atlas_event.id,
                atlas_event.type,
                atlas_event.source,
                atlas_event.correlation_id,
                atlas_event.causation_id,
                dedup_key,
                atlas_event.occurred_at,
                payload_json,
                json.dumps(atlas_event.metadata),
                atlas_event.schema_version,
                DurabilityTier.DURABLE.value,
                DeliveryStatus.PENDING.value,
                0,
            ),
        )

        await self._db.conn.commit()
        self._wake_event.set()

    async def _process_queue(self) -> None:
        while not self._closed:
            try:
                if self._closed:
                    break
                cur = await self._db.conn.execute(
                    "SELECT id, type, payload FROM events "
                    "WHERE delivery_status = 'pending' ORDER BY occurred_at ASC LIMIT 50"
                )
                rows = await cur.fetchall()

                if not rows:
                    self._wake_event.clear()
                    await self._wake_event.wait()
                    continue

                # Atomically claim all events in this batch by transitioning to in_flight.
                # This prevents duplicate delivery if the dispatcher crashes mid-batch.
                ids = [row["id"] for row in rows]
                placeholders = ",".join("?" * len(ids))
                await self._db.conn.execute(
                    f"UPDATE events SET delivery_status = 'in_flight', "
                    f"attempt_count = attempt_count + 1 "
                    f"WHERE id IN ({placeholders}) AND delivery_status = 'pending'",
                    ids,
                )
                await self._db.conn.commit()

                delivered_ids = []
                dead_letter_ids = []
                for row in rows:
                    topic = row["type"]
                    payload_json = row["payload"]
                    eid = row["id"]

                    event_cls = self._event_types.get(topic, Event)
                    try:
                        event = event_cls.model_validate_json(payload_json)
                    except Exception as e:
                        _log.error("bus.deserialize_error", event_type="bus", error=str(e), topic=topic)
                        dead_letter_ids.append((str(e), eid))
                        continue

                    handlers = tuple(self._subs.get(topic, ()))
                    handler_error = False
                    if handlers:
                        results = await asyncio.gather(*(h(event) for h in handlers), return_exceptions=True)
                        for res in results:
                            if isinstance(res, Exception):
                                _log.warning(
                                    "bus.handler_error",
                                    event_type="bus",
                                    topic=topic,
                                    correlation_id=event.correlation_id,
                                    error=repr(res),
                                )
                                handler_error = True

                    # Global subscribers
                    if self._global_subs:
                        global_results = await asyncio.gather(
                            *(g(topic, payload_json) for g in self._global_subs),
                            return_exceptions=True,
                        )
                        for res in global_results:
                            if isinstance(res, Exception):
                                _log.warning("bus.global_handler_error", event_type="bus", error=repr(res))
                                handler_error = True

                    # An event is only "delivered" if dispatch succeeded.
                    # If a handler raised, leave it pending for retry (will be re-claimed next loop).
                    # Only dead-letter on deserialization failures (non-recoverable) or after max retries.
                    if handler_error:
                        # Check if we've exceeded max retries
                        cur = await self._db.conn.execute("SELECT attempt_count FROM events WHERE id = ?", (eid,))
                        attempt_row: Row | None = await cur.fetchone()
                        attempts = attempt_row["attempt_count"] if attempt_row is not None else 1
                        if attempts >= 5:
                            dead_letter_ids.append(("max retries exceeded", eid))
                        # Otherwise leave as pending for retry
                        else:
                            # Reset back to pending for retry
                            await self._db.conn.execute(
                                "UPDATE events SET delivery_status = 'pending' WHERE id = ?",
                                (eid,),
                            )
                    else:
                        delivered_ids.append(eid)

                if delivered_ids:
                    placeholders = ",".join("?" * len(delivered_ids))
                    await self._db.conn.execute(
                        f"UPDATE events SET delivery_status = 'delivered' WHERE id IN ({placeholders})",
                        delivered_ids,
                    )
                if dead_letter_ids:
                    for reason, eid in dead_letter_ids:
                        await self._db.conn.execute(
                            "UPDATE events SET delivery_status = 'dead_letter', dead_letter_reason = ? WHERE id = ?",
                            (reason, eid),
                        )

                if delivered_ids or dead_letter_ids:
                    await self._db.conn.commit()
            except Exception as e:
                if not self._closed:
                    _log.error("bus.process_error", event_type="bus", error=str(e))
                    await asyncio.sleep(1)

    async def close(self) -> None:
        self._closed = True
        self._wake_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._subs.clear()

    async def recover_in_flight(self) -> int:
        """Reset any in_flight events back to pending (crash recovery).

        Call this on startup before starting _process_queue to ensure events
        that were claimed but not delivered due to a crash are retried.

        Returns the count of recovered events.
        """
        cur = await self._db.conn.execute(
            "UPDATE events SET delivery_status = 'pending' WHERE delivery_status = 'in_flight'",
        )
        await self._db.conn.commit()
        recovered = cur.rowcount or 0
        if recovered:
            _log.info("bus.recovered_in_flight", event_type="bus", count=recovered)
        return recovered

    async def replay_dead_letter(self, event_id: str) -> str | None:
        """Move a dead-lettered event back to pending for reprocessing.

        Returns the topic of the replayed event, or None if not found.
        """
        cur = await self._db.conn.execute(
            "SELECT type FROM events WHERE id = ? AND delivery_status = 'dead_letter'",
            (event_id,),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        await self._db.conn.execute(
            "UPDATE events SET delivery_status = 'pending', attempt_count = 0, dead_letter_reason = NULL WHERE id = ?",
            (event_id,),
        )
        await self._db.conn.commit()
        self._wake_event.set()
        return cast("str", row["type"])
