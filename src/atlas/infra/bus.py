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
from typing import Any

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
        
        atlas_event = AtlasEvent(
            id=str(uuid.uuid4()),
            type=topic,
            source="system",
            correlation_id=correlation_id,
            causation_id=task_id,
            occurred_at=now,
            payload=event_dict,
            metadata={"original_type": event.__class__.__name__}
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
                atlas_event.deduplication_key,
                atlas_event.occurred_at,
                payload_json,
                json.dumps(atlas_event.metadata),
                atlas_event.schema_version,
                DurabilityTier.DURABLE.value,
                DeliveryStatus.PENDING.value,
                0
            )
        )

        await self._db.conn.commit()
        self._wake_event.set()

    async def _process_queue(self) -> None:
        while not self._closed:
            try:
                if self._closed:
                    break
                cur = await self._db.conn.execute(
                    "SELECT id, type, payload FROM events WHERE delivery_status = 'pending' ORDER BY occurred_at ASC LIMIT 50"
                )
                rows = await cur.fetchall()

                if not rows:
                    self._wake_event.clear()
                    await self._wake_event.wait()
                    continue

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
                    
                    # Global subscribers
                    if self._global_subs:
                        global_results = await asyncio.gather(*(g(topic, payload_json) for g in self._global_subs), return_exceptions=True)
                        for res in global_results:
                            if isinstance(res, Exception):
                                _log.warning("bus.global_handler_error", event_type="bus", error=repr(res))
                                
                    delivered_ids.append(eid)

                if delivered_ids:
                    await self._db.conn.execute(
                        f"UPDATE events SET delivery_status = 'delivered' WHERE id IN ({','.join('?' * len(delivered_ids))})", 
                        delivered_ids
                    )
                if dead_letter_ids:
                    for reason, eid in dead_letter_ids:
                        await self._db.conn.execute(
                            "UPDATE events SET delivery_status = 'dead_letter', dead_letter_reason = ? WHERE id = ?",
                            (reason, eid)
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
