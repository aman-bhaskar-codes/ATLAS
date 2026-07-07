"""Orchestrator event taxonomy + publisher.

WHY typed events on the L0 bus: observability must be structured and
transport-agnostic. The dashboard (Phase 6/10), reflection (Phase 8), and
supervisor (Phase 9) all subscribe to these same events without the runtime
knowing they exist.
"""

from __future__ import annotations

from dataclasses import field
from typing import Any

from atlas.infra.bus import Event, MessageBus


class OrchestratorEvent(Event):
    task_id: str
    state: str
    kind: str          # 'task.created' | 'planning.started' | 'tool.requested' | ...
    latency_ms: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


class EventPublisher:
    def __init__(self, bus: MessageBus) -> None:
        self._bus = bus

    async def emit(
        self, *, task_id: str, correlation_id: str, state: str, kind: str,
        latency_ms: int = 0, **metadata: Any,
    ) -> None:
        await self._bus.publish("orchestrator", OrchestratorEvent(
            correlation_id=correlation_id, task_id=task_id, state=state,
            kind=kind, latency_ms=latency_ms, metadata=metadata,
        ))
