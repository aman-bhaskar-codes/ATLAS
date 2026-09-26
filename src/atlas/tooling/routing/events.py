"""Route events on the existing MessageBus (Part 3 §56).

One typed event per routing lifecycle beat, on topic ``route``. Payloads carry
ids, layer outputs, and reasons — never secrets, never prompt material.
"""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, Field

from atlas.infra.bus import Event, MessageBus

TOPIC_ROUTE = "route"

KIND_STARTED = "route.started"
KIND_DOMAIN_SELECTED = "route.domain_selected"
KIND_STRATEGY_SELECTED = "route.strategy_selected"
KIND_CAPABILITIES_RESOLVED = "route.capabilities_resolved"
KIND_CANDIDATES_DISCOVERED = "route.candidates_discovered"
KIND_CANDIDATES_FILTERED = "route.candidates_filtered"
KIND_JUDGMENT_COMPLETED = "route.judgment.completed"
KIND_RANKED = "route.ranked"
KIND_COMPILED = "route.compiled"
KIND_FALLBACK_SELECTED = "route.fallback_selected"
KIND_REPLANNED = "route.replanned"
KIND_COMPLETED = "route.completed"
KIND_FAILED = "route.failed"


class RouteEvent(Event):
    model_config = ConfigDict(frozen=True)

    kind: str
    route_id: str = ""
    task_id: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class RouteEventPublisher:
    """Publishes route events; failures are logged, never propagated —
    observability must not break routing."""

    def __init__(self, bus: MessageBus | None) -> None:
        self._bus = bus

    async def publish(self, kind: str, payload: dict[str, Any]) -> None:
        if self._bus is None:
            return
        from atlas.infra.logging import get_logger

        log = get_logger("atlas.tooling.routing.events")
        try:
            await self._bus.publish(
                TOPIC_ROUTE,
                RouteEvent(
                    correlation_id=str(payload.get("correlation_id") or payload.get("route_id") or "route"),
                    kind=kind,
                    route_id=str(payload.get("route_id", "")),
                    task_id=str(payload.get("task_id", "")),
                    payload=payload,
                ),
            )
        except Exception as exc:
            log.warning("route.event.failed", event_type="routing", kind=kind, error=repr(exc))


__all__ = ["TOPIC_ROUTE", "RouteEvent", "RouteEventPublisher"]
