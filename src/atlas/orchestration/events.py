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


class SafetyEvent(Event):
    """Safety engine classification, approval flow, and audit events."""
    task_id: str
    kind: str  # tier.classified | approval.requested | approval.resolved | approval.denied
    tier: str  # AUTO | NOTIFY | CONFIRM | DANGEROUS | FORBIDDEN
    action: str
    tool: str | None = None
    requires_approval: bool = False
    approval_id: str | None = None
    decision: str | None = None
    reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class PlanningEvent(Event):
    """Planning and reasoning lifecycle events with DAG snapshots."""
    task_id: str
    kind: str  # plan.started | plan.dag_updated | plan.completed | reasoning.chunk
    dag_snapshot: dict[str, Any] | None = None
    reasoning_chunk: str | None = None
    step_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


class MemoryEvent(Event):
    """Memory system retrieval, storage, and consolidation events."""
    task_id: str
    kind: str  # memory.retrieved | memory.stored | memory.consolidated | memory.pruned
    memory_type: str  # episodic | semantic | working | user_model
    count: int = 0
    query: str | None = None
    items: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class ToolEvent(Event):
    """Tool execution lifecycle events."""
    task_id: str
    kind: str  # tool.requested | tool.approved | tool.executing | tool.completed | tool.failed
    tool: str
    operation: str | None = None
    args: dict[str, Any] = field(default_factory=dict)
    result: Any | None = None
    error: str | None = None
    latency_ms: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


class FeedbackEvent(Event):
    """User feedback events for preference learning."""
    task_id: str
    kind: str  # feedback.submitted | feedback.processed
    rating: int | None = None  # -1 (negative) or 1 (positive)
    comment: str | None = None
    original_output: str | None = None
    edited_output: str | None = None
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

    async def emit_safety(
        self, *, task_id: str, correlation_id: str, kind: str, tier: str,
        action: str, tool: str | None = None, requires_approval: bool = False,
        approval_id: str | None = None, decision: str | None = None,
        reason: str | None = None, **metadata: Any,
    ) -> None:
        await self._bus.publish("safety", SafetyEvent(
            correlation_id=correlation_id, task_id=task_id, kind=kind,
            tier=tier, action=action, tool=tool, requires_approval=requires_approval,
            approval_id=approval_id, decision=decision, reason=reason, metadata=metadata,
        ))

    async def emit_planning(
        self, *, task_id: str, correlation_id: str, kind: str,
        dag_snapshot: dict[str, Any] | None = None, reasoning_chunk: str | None = None,
        step_count: int = 0, **metadata: Any,
    ) -> None:
        await self._bus.publish("planning", PlanningEvent(
            correlation_id=correlation_id, task_id=task_id, kind=kind,
            dag_snapshot=dag_snapshot, reasoning_chunk=reasoning_chunk,
            step_count=step_count, metadata=metadata,
        ))

    async def emit_memory(
        self, *, task_id: str, correlation_id: str, kind: str, memory_type: str,
        count: int = 0, query: str | None = None, items: list[str] | None = None,
        **metadata: Any,
    ) -> None:
        await self._bus.publish("memory", MemoryEvent(
            correlation_id=correlation_id, task_id=task_id, kind=kind,
            memory_type=memory_type, count=count, query=query,
            items=items or [], metadata=metadata,
        ))

    async def emit_tool(
        self, *, task_id: str, correlation_id: str, kind: str, tool: str,
        operation: str | None = None, args: dict[str, Any] | None = None,
        result: Any | None = None, error: str | None = None,
        latency_ms: int = 0, **metadata: Any,
    ) -> None:
        await self._bus.publish("tool", ToolEvent(
            correlation_id=correlation_id, task_id=task_id, kind=kind,
            tool=tool, operation=operation, args=args or {},
            result=result, error=error, latency_ms=latency_ms, metadata=metadata,
        ))

    async def emit_feedback(
        self, *, task_id: str, correlation_id: str, kind: str,
        rating: int | None = None, comment: str | None = None,
        original_output: str | None = None, edited_output: str | None = None,
        **metadata: Any,
    ) -> None:
        await self._bus.publish("feedback", FeedbackEvent(
            correlation_id=correlation_id, task_id=task_id, kind=kind,
            rating=rating, comment=comment, original_output=original_output,
            edited_output=edited_output, metadata=metadata,
        ))
