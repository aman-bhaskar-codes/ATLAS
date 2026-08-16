"""Orchestrator facade — the one entrypoint for the required pipeline.

PIPELINE (no shortcuts): create -> build context -> route -> plan -> reason
(-> tools via safety) -> record -> result. Every stage transitions the state
machine and emits an event. Transports (CLI/voice/API) call run() and know
nothing of the internals.

Phase 2 additions:
  - Trajectory storage: save complete execution history after task completion
  - Experience extraction: async post-task analysis to extract lessons (doesn't block)
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, Any

from atlas.infra.clock import Clock
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
from atlas.orchestration.stores import CancellationStore, ExecutionStore
from atlas.orchestration.types import Task, TaskResult

if TYPE_CHECKING:
    from atlas.memory.experience_extractor import ExperienceExtractor
    from atlas.memory.trajectory_store import TrajectoryStore

_log = get_logger("atlas.orch")

_SAFETY_CONSTRAINTS = (
    "You operate under deny-by-default. Consequential actions require confirmation. "
    "Prefer reversible, least-privilege actions. Never fabricate tool results."
)


class Orchestrator:
    def __init__(
        self,
        *,
        ids: IdGenerator,
        clock: Clock,
        execution_store: Any,
        cancellation_store: Any,
        router: Router,
        planner: Planner,
        context_builder: ContextBuilder,
        reasoning: ReasoningLoop,
        registry: ToolRegistry,
        events: EventPublisher,
        trajectory_store: TrajectoryStore | None = None,  # Phase 2
        experience_extractor: ExperienceExtractor | None = None,  # Phase 2
    ) -> None:
        self._ids = ids
        self._clock = clock
        self._exec_store: ExecutionStore = execution_store
        self._cancel_store: CancellationStore = cancellation_store
        self._router = router
        self._planner = planner
        self._context = context_builder
        self._reasoning = reasoning
        self._registry = registry
        self._events = events
        self._trajectory_store = trajectory_store
        self._experience_extractor = experience_extractor
        self._cancels: dict[str, CancellationToken] = {}

    def cancel(self, task_id: str) -> None:
        if (tok := self._cancels.get(task_id)) is not None:
            tok.cancel()
        # Also persist cancellation intent (survives crash).
        try:
            asyncio.get_event_loop().create_task(self._cancel_store.request_cancellation(task_id))
        except RuntimeError:
            pass  # no event loop yet (tests)

    async def run(self, event: InboundEvent) -> TaskResult:
        task = Task(
            id=self._ids.task_id(),
            correlation_id=event.correlation_id,
            source=event.source,
            request=event.content,
            created_ts=self._clock.now(),
        )
        machine = TaskStateMachine()
        token = CancellationToken()
        self._cancels[task.id] = token
        started = time.perf_counter()
        await self._exec_store.create_task(
            task_id=task.id,
            source=task.source,
            payload_json=json.dumps({"request": task.request, "correlation_id": task.correlation_id}),
            idempotency_key=None,
            created_ts=task.created_ts,
        )
        await self._events.emit(
            task_id=task.id, correlation_id=task.correlation_id, state=machine.state.value, kind="task.created"
        )
        try:
            machine.transition(TaskState.READY)
            await self._events.emit(
                task_id=task.id, correlation_id=task.correlation_id, state=machine.state.value, kind="task.started"
            )
            machine.transition(TaskState.BUILDING_CONTEXT)
            await self._events.emit(
                task_id=task.id, correlation_id=task.correlation_id, state=machine.state.value, kind="context.building"
            )
            caps = await self._router.route(task.request, task.correlation_id)
            context = await self._context.build(
                task.request,
                safety_constraints=_SAFETY_CONSTRAINTS,
                tool_catalog=self._registry.catalog(),
                task_id=task.id,
                correlation_id=task.correlation_id,
            )

            machine.transition(TaskState.PLANNING)
            await self._events.emit(
                task_id=task.id, correlation_id=task.correlation_id, state=machine.state.value, kind="planning.started"
            )
            plan = await self._planner.plan(task.request, context, caps, task.correlation_id)
            await self._events.emit(
                task_id=task.id,
                correlation_id=task.correlation_id,
                state=machine.state.value,
                kind="planning.finished",
                steps=len(plan.steps),
                risk=plan.risk.value,
                confidence=plan.confidence,
            )

            # Phase 1: Build GoalState from plan so the loop can track and replan
            goal = GoalState(
                objective=plan.goal,
                constraints=list(plan.constraints),
                current_state="planning_complete",
                confidence=plan.confidence,
            )

            result = await self._reasoning.run(
                task_id=task.id,
                correlation_id=task.correlation_id,
                plan=plan,
                context=context,
                machine=machine,
                token=token,
                goal=goal,
                caps=caps,
            )

            # Phase 2: Save trajectory for durable learning
            if self._trajectory_store and result.actions:
                await self._save_trajectory(
                    task=task,
                    plan=plan,
                    result=result,
                    goal=goal,
                )

            await self._events.emit(
                task_id=task.id,
                correlation_id=task.correlation_id,
                state=machine.state.value,
                kind="task.completed" if result.ok else "task.failed",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
            return result
        finally:
            self._cancels.pop(task.id, None)
            await self._exec_store.update_task_state(
                task_id=task.id,
                state=machine.state.value,
                updated_ts=self._clock.now(),
            )

    async def _save_trajectory(
        self,
        *,
        task: Task,
        plan: object,
        result: TaskResult,
        goal: GoalState,
    ) -> None:
        """Save trajectory and trigger async experience extraction.

        Phase 2: Captures complete execution history for durable learning.
        Experience extraction runs async (doesn't block task return).
        """
        from atlas.memory.trajectory import Trajectory

        _plan_risk: Any = getattr(plan, "risk", "low")
        # Build Trajectory object from task execution data
        trajectory = Trajectory(
            id=self._ids.execution_id(),
            task_id=task.id,
            correlation_id=task.correlation_id,
            request=task.request,
            goal=getattr(plan, "goal", "unknown"),
            plan_steps=tuple(getattr(step, "intent", str(step)) for step in getattr(plan, "steps", [])),
            risk_level=_plan_risk.value if hasattr(_plan_risk, "value") else "low",
            plan_confidence=getattr(plan, "confidence", 0.5),
            actions=result.actions,
            observations=result.observations,
            decision_traces=(),  # TODO: Collect from replanner/router in future
            failure_records=(),  # TODO: Collect from error handlers in future
            replan_count=result.replan_count,
            verification_passed=result.verification_passed,
            verification_score=result.verification_score,
            success=result.ok,
            answer=result.answer,
            error=result.error,
            steps_taken=result.steps_taken,
            latency_ms=result.latency_ms,
            tokens_used=result.tokens_used,
            cost_usd=0.0,  # TODO: Calculate from model gateway costs
            model_calls=result.model_calls,
            tool_calls=result.tool_calls,
            created_ts=task.created_ts,
            completed_ts=self._clock.now(),
        )

        # Save trajectory (< 50ms, synchronous)
        try:
            trajectory_id = await self._trajectory_store.save_trajectory(trajectory)  # type: ignore[union-attr]

            _log.info(
                "trajectory.saved",
                event_type="orchestration",
                trajectory_id=trajectory_id,
                task_id=task.id,
                success=trajectory.success,
                steps=trajectory.steps_taken,
            )

            # Phase 2: Trigger async experience extraction (doesn't block)
            if self._experience_extractor:
                asyncio.create_task(self._extract_experiences_async(trajectory))

        except Exception as exc:
            # Don't fail the task if trajectory save fails
            _log.error(
                "trajectory.save_failed",
                event_type="orchestration",
                error=repr(exc),
                task_id=task.id,
            )

    async def _extract_experiences_async(self, trajectory: object) -> None:
        """Async experience extraction (runs in background).

        Doesn't block task return. Extracts 0-3 lessons per trajectory.
        Target: < 3s per trajectory.
        """
        try:
            from atlas.memory.trajectory import Trajectory

            if not isinstance(trajectory, Trajectory):
                return

            experiences = await self._experience_extractor.extract_from_trajectory(trajectory)  # type: ignore[union-attr]

            if experiences:
                _log.info(
                    "experience.extracted",
                    event_type="orchestration",
                    trajectory_id=trajectory.id,
                    count=len(experiences),
                    categories=[e.category.value for e in experiences],
                )
            else:
                _log.debug(
                    "experience.none_extracted",
                    event_type="orchestration",
                    trajectory_id=trajectory.id,
                )

        except Exception as exc:
            # Log error but don't crash (this is async)
            _log.error(
                "experience.extraction_failed",
                event_type="orchestration",
                error=repr(exc),
                trajectory_id=getattr(trajectory, "id", "unknown"),
            )
