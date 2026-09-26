"""Execution events on the existing MessageBus (Part 4 §48-§50/§83).

Immutable, serializable, append-only snapshots with per-run sequence numbers.
Never mutated after emission; never carry secrets.
"""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, Field

from atlas.infra.bus import Event, MessageBus

TOPIC_EXECUTION = "execution"

KIND_RUN_CREATED = "execution.created"
KIND_RUN_READY = "execution.ready"
KIND_RUN_STARTED = "execution.started"
KIND_STEP_READY = "step.ready"
KIND_STEP_STARTED = "step.started"
KIND_STEP_COMPLETED = "step.completed"
KIND_STEP_FAILED = "step.failed"
KIND_STEP_RETRYING = "step.retrying"
KIND_STEP_TIMED_OUT = "step.timed_out"
KIND_STEP_CANCELLED = "step.cancelled"
KIND_FALLBACK_STARTED = "fallback.started"
KIND_FALLBACK_SELECTED = "fallback.selected"
KIND_RUN_PAUSED = "execution.paused"
KIND_RUN_RESUMED = "execution.resumed"
KIND_RUN_REPLANNED = "execution.replanned"
KIND_RUN_COMPLETED = "execution.completed"
KIND_RUN_FAILED = "execution.failed"
KIND_RUN_CANCELLED = "execution.cancelled"


class ExecutionEvent(Event):
    model_config = ConfigDict(frozen=True)

    kind: str
    run_id: str = ""
    task_id: str = ""
    step_id: str = ""
    sequence: int = 0
    payload: dict[str, Any] = Field(default_factory=dict)


class ExecutionEventPublisher:
    """Append-only event emission with per-run sequence numbers (§83).
    Observability failures are logged, never propagated into execution."""

    def __init__(self, bus: MessageBus | None) -> None:
        self._bus = bus
        self._sequences: dict[str, int] = {}

    def next_sequence(self, run_id: str) -> int:
        self._sequences[run_id] = self._sequences.get(run_id, 0) + 1
        return self._sequences[run_id]

    async def publish(
        self,
        kind: str,
        *,
        run_id: str = "",
        task_id: str = "",
        step_id: str = "",
        correlation_id: str = "",
        **payload: Any,
    ) -> None:
        if self._bus is None:
            return
        from atlas.infra.logging import get_logger

        log = get_logger("atlas.tooling.execution.events")
        try:
            await self._bus.publish(
                TOPIC_EXECUTION,
                ExecutionEvent(
                    correlation_id=correlation_id or run_id or "execution",
                    kind=kind,
                    run_id=run_id,
                    task_id=task_id,
                    step_id=step_id,
                    sequence=self.next_sequence(run_id) if run_id else 0,
                    payload=payload,
                ),
            )
        except Exception as exc:
            log.warning(
                "execution.event.failed",
                event_type="execution",
                kind=kind,
                run_id=run_id,
                error=repr(exc),
            )


__all__ = ["TOPIC_EXECUTION", "ExecutionEvent", "ExecutionEventPublisher"]
