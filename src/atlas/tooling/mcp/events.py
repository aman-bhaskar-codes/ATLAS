"""MCP events on the existing bus (Part 5 §70-§72)."""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, Field

from atlas.infra.bus import Event, MessageBus

TOPIC_MCP = "mcp"


class MCPEvent(Event):
    model_config = ConfigDict(frozen=True)

    kind: str
    server_id: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class MCPEventPublisher:
    """Publishes mcp.* events; failures logged, never propagated. Never
    carries secrets (§27)."""

    def __init__(self, bus: MessageBus | None) -> None:
        self._bus = bus

    async def publish(self, kind: str, **payload: Any) -> None:
        if self._bus is None:
            return
        from atlas.infra.logging import get_logger

        log = get_logger("atlas.tooling.mcp.events")
        try:
            await self._bus.publish(
                TOPIC_MCP,
                MCPEvent(
                    correlation_id=str(payload.get("correlation_id") or payload.get("server_id") or "mcp"),
                    kind=kind,
                    server_id=str(payload.get("server_id", "")),
                    payload=payload,
                ),
            )
        except Exception as exc:
            log.warning("mcp.event.failed", event_type="mcp", kind=kind, error=repr(exc))


__all__ = ["TOPIC_MCP", "MCPEvent", "MCPEventPublisher"]
