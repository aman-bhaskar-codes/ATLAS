"""Bounded OTAR reasoning loop — the heart of the runtime.

OTAR: Observe → Think → Act → Reflect. Each iteration drives the state machine
through concrete states and every external call is timed, retried (if
recoverable), and audited. The loop CANNOT run forever: limits raise typed errors
the monitor turns into a graceful FAILED. Tool actions go through the dispatcher
(Safety Engine); final/ask actions terminate. Pre-action critique runs before
consequential actions (Phase 4.5). Post-action reflection evaluates outcomes.

Phase 1 additions:
  - GoalState tracks desired vs current state and replan budget
  - Replanner produces a revised plan after tool failure or verification miss
  - Verifier checks the final answer against success criteria before returning

Phase 2 additions:
  - Trajectory capture: records complete execution history (actions, observations)
  - DecisionTrace: logs model selection, tool choices, replanning decisions
  - FailureRecord: structured error tracking for taxonomy building
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from atlas.infra.ids import CorrelationId, TaskId
from atlas.infra.logging import get_logger
from atlas.infra.types import ModelCapability, ModelRequest, Tier
from atlas.intelligence.gateway import ModelGateway
from atlas.memory.types import Episode, EpisodeKind
from atlas.orchestration.dispatcher import ToolDispatcher
from atlas.orchestration.errors import CancellationError, OrchestrationError
from atlas.orchestration.events import EventPublisher
from atlas.orchestration.goal import GoalState, NullVerifier, VerificationResult, Verifier
from atlas.orchestration.limits import ExecutionLimits, LimitCounter
from atlas.orchestration.managers.cancellation import CancellationToken
from atlas.orchestration.managers.retry import RetryManager
from atlas.orchestration.managers.timeout import with_timeout
from atlas.orchestration.monitor import ExecutionMonitor
from atlas.orchestration.parser import ResponseParser
from atlas.orchestration.prompt_builder import PromptBuilder
from atlas.orchestration.recorder import ExecutionRecorder
from atlas.orchestration.reflection import ReflectionHook
from atlas.orchestration.replanner import Replanner
from atlas.orchestration.state import TaskState, TaskStateMachine
from atlas.orchestration.types import (
    Action,
    Capabilities,
    Observation,
    Plan,
    TaskResult,
    Thought,
)
from atlas.orchestration.validator import OutputValidator

if TYPE_CHECKING:
    from atlas.memory.trajectory_store import TrajectoryStore
    from atlas.memory.working import WorkingMemory

_log = get_logger("atlas.orch.reasoning")


class ReasoningLoop:
    def __init__(
        self, *, gateway: ModelGateway, dispatcher: ToolDispatcher,
        parser: ResponseParser, validator: OutputValidator, prompts: PromptBuilder,
        recorder: ExecutionRecorder, monitor: ExecutionMonitor,
        retry: RetryManager, reflection: ReflectionHook, events: EventPublisher,
        limits: ExecutionLimits, model_timeout_s: float = 120.0,
        working: "WorkingMemory | None" = None,
        replanner: Replanner | None = None,       # Phase 1: bounded replanning
        verifier: Verifier | None = None,         # Phase 1: answer verification
        trajectory_store: "TrajectoryStore | None" = None,  # Phase 2: trajectory capture
    ) -> None:
        self._gw = gateway
        self._dispatch = dispatcher
        self._parser = parser
        self._validator = validator
        self._prompts = prompts
        self._recorder = recorder
        self._monitor = monitor
        self._retry = retry
        self._reflection = reflection
        self._events = events
        self._limits = limits
        self._model_timeout_s = model_timeout_s
        self._working = working
        self._replanner = replanner
        self._verifier: Verifier = verifier if verifier is not None else NullVerifier()
        self._trajectory_store = trajectory_store

    async def run(
        self, *, task_id: TaskId, correlation_id: CorrelationId, plan: Plan, context: str,
        machine: TaskStateMachine, token: CancellationToken,
        goal: GoalState | None = None,
        caps: Capabilities | None = None,
    ) -> TaskResult:
        """Execute the OTAR loop.

        Phase 1 additions:
          - goal:  if provided, enables verification and replanning
          - caps:  forwarded to Replanner for capability-aware plan revision
        
        Phase 2 additions:
          - Trajectory capture: collects actions, observations, decision traces
          - Returns trajectory data in TaskResult for orchestrator to save
        """
        # Build a GoalState from the plan when none is provided (backward compat)
        if goal is None:
            goal = GoalState(objective=plan.goal)

        counter = LimitCounter(self._limits)
        history: list[tuple[Thought, Observation | None]] = []
        current_plan = plan
        verification: VerificationResult | None = None
        
        # Phase 2: Trajectory capture initialization
        from atlas.memory.trajectory import ActionRecord, ObservationRecord
        trajectory_actions: list[ActionRecord] = []
        trajectory_observations: list[ObservationRecord] = []
        trajectory_start = time.perf_counter()

        while True:
            try:
                self._monitor.check_may_continue(token)
                counter.tick_step()
                machine.transition(TaskState.REASONING)

                thought, action = await self._reason_once(
                    task_id, correlation_id, current_plan, context, history, counter,
                )
                await self._events.emit(
                    task_id=task_id, correlation_id=correlation_id, state=machine.state.value,
                    kind="reasoning.thought", step=counter.steps, thought=thought.content[:200]
                )
                await self._recorder.record_thought(correlation_id, thought)
                await self._recorder.record_action(correlation_id, action)
                await self._events.emit(
                    task_id=task_id, correlation_id=correlation_id, state=machine.state.value,
                    kind="reasoning.action", step=counter.steps, action_kind=action.kind, tool=action.tool
                )
                
                # Phase 2: Capture action for trajectory
                trajectory_actions.append(ActionRecord(
                    step=action.step,
                    kind=action.kind,
                    tool=action.tool,
                    operation=action.operation,
                    args=action.args,
                    final_text=action.final_text,
                ))

                if action.kind in ("final_answer", "ask_user"):
                    # ── Phase 1: Verify the answer before returning ──────────
                    machine.transition(TaskState.VALIDATING)
                    answer_text = action.final_text or ""
                    verification = await self._verifier.verify(goal, answer_text, context)

                    if not verification.passed and goal.can_replan() and self._replanner:
                        # Verification failed — try replanning
                        await self._events.emit(
                            task_id=task_id, correlation_id=correlation_id,
                            state=machine.state.value,
                            kind="replan.started",
                            replan_count=goal.replan_count + 1,
                            trigger="verification_failed",
                            score=verification.score,
                        )
                        failure_context = (
                            f"The answer failed verification "
                            f"(score {verification.score:.2f}). "
                            f"Reason: {verification.failure_reason or 'unspecified'}. "
                            f"Suggestions: {'; '.join(verification.suggestions) or 'none'}. "
                            f"Previous answer: {answer_text[:400]}"
                        )
                        goal.record_replan()
                        current_plan = await self._replanner.replan(
                            goal=goal,
                            original_plan=current_plan,
                            failure_context=failure_context,
                            correlation_id=correlation_id,
                            caps=caps,
                        )
                        # Summarise history to stay within context budget
                        history = history[-3:]
                        await self._events.emit(
                            task_id=task_id, correlation_id=correlation_id,
                            state=machine.state.value,
                            kind="replan.finished",
                            replan_count=goal.replan_count,
                            new_goal=current_plan.goal,
                        )
                        machine.transition(TaskState.REASONING)
                        continue

                    machine.transition(TaskState.COMPLETED)
                    trajectory_latency_ms = int((time.perf_counter() - trajectory_start) * 1000)
                    
                    return TaskResult(
                        task_id=task_id, ok=True,
                        answer=answer_text,
                        steps_taken=counter.steps,
                        replan_count=goal.replan_count,
                        verification_passed=verification.passed,
                        verification_score=verification.score,
                        # Phase 2: Trajectory data
                        actions=tuple(trajectory_actions),
                        observations=tuple(trajectory_observations),
                        latency_ms=trajectory_latency_ms,
                        tokens_used=counter.total_tokens,
                        model_calls=counter.steps,  # Rough approximation
                        tool_calls=counter.tools,
                    )

                if action.kind == "noop":
                    machine.transition(TaskState.VALIDATING)
                    history.append((thought, None))
                    continue

                # ── Tool call: critique → dispatch → observe → reflect ───────
                action = await self._reflection.critique(action, context)
                counter.tick_tool()
                machine.transition(TaskState.WAITING_TOOL)
                await self._events.emit(
                    task_id=task_id, correlation_id=correlation_id, state=machine.state.value,
                    kind="tool.requested", tool=action.tool, operation=action.operation, args=action.args
                )
                self._monitor.check_may_continue(token)
                machine.transition(TaskState.EXECUTING)
                await self._events.emit(
                    task_id=task_id, correlation_id=correlation_id, state=machine.state.value,
                    kind="tool.executing", tool=action.tool
                )

                async def _do_dispatch(a: Action = action) -> Observation:
                    return await self._dispatch.dispatch(a, correlation_id)

                obs = await self._retry.run(_do_dispatch, counter)
                await self._events.emit(
                    task_id=task_id, correlation_id=correlation_id, state=machine.state.value,
                    kind="tool.completed" if obs.ok else "tool.failed",
                    tool=action.tool, ok=obs.ok, error=obs.error
                )
                machine.transition(TaskState.OBSERVING)
                await self._recorder.record_observation(correlation_id, obs)
                
                # Phase 2: Capture observation for trajectory
                trajectory_observations.append(ObservationRecord(
                    step=obs.step,
                    ok=obs.ok,
                    content=str(obs.content)[:1000] if obs.content else None,  # Truncate for storage
                    error=obs.error,
                ))

                # Phase 0: Push observation into WorkingMemory
                if self._working:
                    self._working.add(Episode(
                        correlation_id=correlation_id,
                        task_id=task_id,
                        ts=datetime.now(UTC),
                        kind=EpisodeKind.OBSERVATION,
                        role="tool",
                        content=str(obs.content)[:500] if obs.ok else str(obs.error)[:500],
                        tool=action.tool,
                        outcome="success" if obs.ok else "failure",
                        salience=0.5,
                    ))

                # ── Phase 1: Replan on tool dispatch failure ─────────────────
                if (
                    not obs.ok
                    and self._replanner
                    and await self._replanner.should_replan(goal, obs)
                ):
                    await self._events.emit(
                        task_id=task_id, correlation_id=correlation_id,
                        state=machine.state.value,
                        kind="replan.started",
                        replan_count=goal.replan_count + 1,
                        trigger="tool_failure",
                        tool=action.tool,
                        error=obs.error,
                    )
                    failure_context = (
                        f"Tool '{action.tool}' (operation={action.operation}) failed: "
                        f"{obs.error or 'unknown error'}. "
                        f"Tried: {action.args}. "
                        f"Step {counter.steps} of the plan."
                    )
                    goal.record_replan()
                    current_plan = await self._replanner.replan(
                        goal=goal,
                        original_plan=current_plan,
                        failure_context=failure_context,
                        correlation_id=correlation_id,
                        caps=caps,
                    )
                    history = history[-3:]
                    await self._events.emit(
                        task_id=task_id, correlation_id=correlation_id,
                        state=machine.state.value,
                        kind="replan.finished",
                        replan_count=goal.replan_count,
                        new_goal=current_plan.goal,
                    )

                # OTAR Reflect step
                reflection = await self._reflection.reflect(action, obs, context)
                if reflection.learnings:
                    _log.info("reasoning.reflect", event_type="orchestration",
                              correlation_id=correlation_id, step=counter.steps,
                              learnings=reflection.learnings,
                              succeeded=reflection.succeeded)

                history.append((thought, obs))

            except CancellationError as exc:
                machine.transition(TaskState.CANCELLING)
                machine.transition(TaskState.FAILED)
                trajectory_latency_ms = int((time.perf_counter() - trajectory_start) * 1000)
                return TaskResult(
                    task_id=task_id, ok=False, error=str(exc),
                    steps_taken=counter.steps,
                    replan_count=goal.replan_count,
                    actions=tuple(trajectory_actions),
                    observations=tuple(trajectory_observations),
                    latency_ms=trajectory_latency_ms,
                    tokens_used=counter.total_tokens,
                    model_calls=counter.steps,
                    tool_calls=counter.tools,
                )
            except OrchestrationError as exc:
                await self._events.emit(task_id=task_id, correlation_id=correlation_id,
                                        state=machine.state.value, kind="task.failed",
                                        error=str(exc))
                machine.transition(TaskState.FAILED)
                trajectory_latency_ms = int((time.perf_counter() - trajectory_start) * 1000)
                return TaskResult(
                    task_id=task_id, ok=False, error=str(exc),
                    steps_taken=counter.steps,
                    replan_count=goal.replan_count,
                    actions=tuple(trajectory_actions),
                    observations=tuple(trajectory_observations),
                    latency_ms=trajectory_latency_ms,
                    tokens_used=counter.total_tokens,
                    model_calls=counter.steps,
                    tool_calls=counter.tools,
                )

    async def _reason_once(
        self, task_id: TaskId, correlation_id: CorrelationId, plan: Plan, context: str,
        history: list[tuple[Thought, Observation | None]], counter: LimitCounter,
    ) -> tuple[Thought, Action]:
        prompt = self._prompts.build_step_prompt(
            context=context, goal=plan.goal, history=history, step=counter.steps,
        )
        started = time.perf_counter()
        resp = await with_timeout(
            self._gw.complete(ModelRequest(
                correlation_id=correlation_id, prompt=prompt,
                required_capabilities=frozenset({
                    ModelCapability.REASONING,
                    ModelCapability.TOOL_CALLING,
                    ModelCapability.JSON_GENERATION,
                }),
                needs_deep_reasoning=(plan.confidence < 0.6),
                stakes_tier=Tier.CONFIRM if plan.risk.value != "low" else Tier.AUTO,
                max_tokens=2048,
            )),
            seconds=self._model_timeout_s, what="model.complete",
        )
        counter.add_tokens(resp.cost.input_tokens + resp.cost.output_tokens)
        await self._events.emit(
            task_id=task_id, correlation_id=correlation_id, state="reasoning",
            kind="reasoning.step", latency_ms=int((time.perf_counter() - started) * 1000),
            step=counter.steps,
        )
        thought, action = self._parser.parse(resp.text, counter.steps)
        self._validator.validate(action)
        return thought, action
