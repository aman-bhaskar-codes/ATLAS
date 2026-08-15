"""Orchestrator facade — the one entrypoint for the required pipeline.

PIPELINE (no shortcuts): create -> build context -> route -> plan -> reason
(-> tools via safety) -> record -> result. Every stage transitions the state
machine and emits an event. Transports (CLI/voice/API) call run() and know
nothing of the internals.
"""

from __future__ import annotations

import json
import time

from atlas.infra.clock import Clock
from atlas.infra.db import Database
from atlas.infra.ids import IdGenerator
from atlas.infra.logging import get_logger
from atlas.infra.types import InboundEvent
from atlas.orchestration.context_builder import ContextBuilder
from atlas.orchestration.events import EventPublisher
from atlas.orchestration.goal import GoalState
from atlas.orchestration.managers.cancellation import CancellationToken
from atlas.orchestration.planner import Planner
from atlas.orchestration.reasoning import ReasoningLoop
from atlas.orchestration.registry import ToolRegistry
from atlas.orchestration.router import Router
from atlas.orchestration.state import TaskState, TaskStateMachine
from atlas.orchestration.types import Task, TaskResult

_log = get_logger("atlas.orch")

_SAFETY_CONSTRAINTS = (
    "You operate under deny-by-default. Consequential actions require confirmation. "
    "Prefer reversible, least-privilege actions. Never fabricate tool results."
)


class Orchestrator:
    def __init__(
        self, *, ids: IdGenerator, clock: Clock, db: Database, router: Router, planner: Planner,
        context_builder: ContextBuilder, reasoning: ReasoningLoop,
        registry: ToolRegistry, events: EventPublisher,
    ) -> None:
        self._ids = ids
        self._clock = clock
        self._db = db
        self._router = router
        self._planner = planner
        self._context = context_builder
        self._reasoning = reasoning
        self._registry = registry
        self._events = events
        self._cancels: dict[str, CancellationToken] = {}

    def cancel(self, task_id: str) -> None:
        if (tok := self._cancels.get(task_id)) is not None:
            tok.cancel()

    async def run(self, event: InboundEvent) -> TaskResult:
        task = Task(
            id=self._ids.task_id(), correlation_id=event.correlation_id,
            source=event.source, request=event.content, created_ts=self._clock.now(),
        )
        machine = TaskStateMachine()
        token = CancellationToken()
        self._cancels[task.id] = token
        started = time.perf_counter()
        await self._db.conn.execute(
            "INSERT OR IGNORE INTO tasks(id, source, state, payload, "
            "idempotency_key, created_ts, updated_ts) VALUES (?,?,?,?,?,?,?)",
            (task.id, task.source, "created",
             json.dumps({"request": task.request, "correlation_id": task.correlation_id}),
             None,
             task.created_ts.isoformat(), task.created_ts.isoformat()),
        )
        await self._db.conn.commit()
        await self._events.emit(task_id=task.id, correlation_id=task.correlation_id,
                                state=machine.state.value, kind="task.created")
        try:
            machine.transition(TaskState.READY)
            await self._events.emit(task_id=task.id, correlation_id=task.correlation_id,
                                    state=machine.state.value, kind="task.started")
            machine.transition(TaskState.BUILDING_CONTEXT)
            await self._events.emit(task_id=task.id, correlation_id=task.correlation_id,
                                    state=machine.state.value, kind="context.building")
            caps = await self._router.route(task.request, task.correlation_id)
            context = await self._context.build(
                task.request, safety_constraints=_SAFETY_CONSTRAINTS,
                tool_catalog=self._registry.catalog(),
                task_id=task.id, correlation_id=task.correlation_id,
            )

            machine.transition(TaskState.PLANNING)
            await self._events.emit(task_id=task.id, correlation_id=task.correlation_id,
                                    state=machine.state.value, kind="planning.started")
            plan = await self._planner.plan(task.request, context, caps, task.correlation_id)
            await self._events.emit(task_id=task.id, correlation_id=task.correlation_id,
                                    state=machine.state.value, kind="planning.finished",
                                    steps=len(plan.steps), risk=plan.risk.value,
                                    confidence=plan.confidence)

            # Phase 1: Build GoalState from plan so the loop can track and replan
            goal = GoalState(
                objective=plan.goal,
                constraints=list(plan.constraints),
                current_state="planning_complete",
                confidence=plan.confidence,
            )

            result = await self._reasoning.run(
                task_id=task.id, correlation_id=task.correlation_id, plan=plan,
                context=context, machine=machine, token=token,
                goal=goal, caps=caps,
            )
            await self._events.emit(
                task_id=task.id, correlation_id=task.correlation_id,
                state=machine.state.value,
                kind="task.completed" if result.ok else "task.failed",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
            return result
        finally:
            self._cancels.pop(task.id, None)
            await self._db.conn.execute(
                "UPDATE tasks SET state=?, updated_ts=? WHERE id=?",
                (machine.state.value, self._clock.now().isoformat(), task.id),
            )
            await self._db.conn.commit()
