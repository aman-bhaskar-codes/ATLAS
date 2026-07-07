"""Typed message bus + event taxonomy.

WHY concurrent-with-isolation delivery: handlers should run in parallel, but one
handler raising must not stop the others or the publisher. WHY typed Event base:
no stringly-typed payloads cross the bus.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable

from pydantic import BaseModel

from atlas.infra.errors import BusError
from atlas.infra.logging import get_logger

_log = get_logger("atlas.bus")


class Event(BaseModel):
    """Base for all bus events. Subclasses add typed fields."""

    model_config = {"frozen": True}
    correlation_id: str


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
    def __init__(self) -> None:
        self._subs: dict[str, list[Handler]] = defaultdict(list)
        self._closed = False

    def subscribe(self, topic: str, handler: Handler) -> None:
        self._subs[topic].append(handler)

    async def publish(self, topic: str, event: Event) -> None:
        if self._closed:
            raise BusError("publish on a closed bus")
        handlers = tuple(self._subs.get(topic, ()))
        if not handlers:
            return
        results = await asyncio.gather(
            *(h(event) for h in handlers), return_exceptions=True
        )
        for res in results:
            if isinstance(res, Exception):
                _log.warning(
                    "bus.handler_error", event_type="bus", topic=topic,
                    correlation_id=event.correlation_id, error=repr(res),
                )

    async def close(self) -> None:
        self._closed = True
        self._subs.clear()
