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
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from atlas.infra.cognition import Evidence, TaskDomain, TaskIntent
from atlas.infra.ids import CorrelationId, TaskId
from atlas.infra.logging import get_logger
from atlas.infra.types import ModelCapability, ModelRequest, Tier
from atlas.intelligence.gateway import ModelGateway
from atlas.memory.trajectory import (
    DecisionOutcome,
    DecisionPoint,
    DecisionTrace,
    FailureCategory,
)
from atlas.memory.types import Episode, EpisodeKind
from atlas.orchestration.context_engine import ContextCompactor
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
    from atlas.memory.trajectory import DecisionTrace
    from atlas.memory.trajectory_store import TrajectoryStore
    from atlas.memory.working import WorkingMemory

_log = get_logger("atlas.orch.reasoning")


class ReasoningLoop:
    def __init__(
        self,
        *,
        gateway: ModelGateway,
        dispatcher: ToolDispatcher,
        parser: ResponseParser,
        validator: OutputValidator,
        prompts: PromptBuilder,
        recorder: ExecutionRecorder,
        monitor: ExecutionMonitor,
        retry: RetryManager,
        reflection: ReflectionHook,
        events: EventPublisher,
        limits: ExecutionLimits,
        model_timeout_s: float = 120.0,
        working: WorkingMemory | None = None,
        replanner: Replanner | None = None,  # Phase 1: bounded replanning
        verifier: Verifier | None = None,  # Phase 1: answer verification
        trajectory_store: TrajectoryStore | None = None,  # Phase 2: trajectory capture
        compactor: ContextCompactor | None = None,  # Batch 5: bounded context
        checkpoint_store: Any = None,  # Batch 7: durable progress
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
        self._compactor = compactor or ContextCompactor()
        self._checkpoints = checkpoint_store

    async def run(
        self,
        *,
        task_id: TaskId,
        correlation_id: CorrelationId,
        plan: Plan,
        context: str,
        machine: TaskStateMachine,
        token: CancellationToken,
        goal: GoalState | None = None,
        caps: Capabilities | None = None,
        intent: TaskIntent | None = None,
    ) -> TaskResult:
        """Execute the OTAR loop.

        Phase 1 additions:
          - goal:  if provided, enables verification and replanning
          - caps:  forwarded to Replanner for capability-aware plan revision

        Phase 2 additions:
          - Trajectory capture: collects actions, observations, decision traces
          - Returns trajectory data in TaskResult for orchestrator to save
          - intent: selects the domain-appropriate verifier (Phase 12). Without
            it every task falls back to model judgement, which is the weakest
            available check.
        """
        # Build a GoalState from the plan when none is provided (backward compat)
        if goal is None:
            goal = GoalState(objective=plan.goal)
        domain = intent.domain if intent is not None else TaskDomain.UNKNOWN

        counter = LimitCounter(self._limits)
        history: list[tuple[Thought, Observation | None]] = []
        current_plan = plan
        verification: VerificationResult | None = None
        # Phase 12/14: bounded provenance the verifier is allowed to rely on.
        # Summaries only — raw tool output never travels with this.
        evidence: list[Evidence] = []

        # Phase 2: Trajectory capture initialization
        from atlas.memory.trajectory import ActionRecord, ObservationRecord
        from atlas.memory.trajectory import FailureRecord as FailureRecordModel

        trajectory_actions: list[ActionRecord] = []
        trajectory_observations: list[ObservationRecord] = []
        trajectory_start = time.perf_counter()

        # Phase 3: Decision trace and failure record collections
        decision_traces: list[DecisionTrace] = []
        failure_records: list[FailureRecordModel] = []

        while True:
            try:
                self._monitor.check_may_continue(token)
                counter.tick_step()
                machine.transition(TaskState.REASONING)

                thought, action, model_trace = await self._reason_once(
                    task_id,
                    correlation_id,
                    current_plan,
                    context,
                    history,
                    counter,
                )
                if model_trace is not None:
                    decision_traces.append(model_trace)
                await self._events.emit(
                    task_id=task_id,
                    correlation_id=correlation_id,
                    state=machine.state.value,
                    kind="reasoning.thought",
                    step=counter.steps,
                    thought=thought.content[:200],
                )
                await self._recorder.record_thought(correlation_id, thought)
                await self._recorder.record_action(correlation_id, action)
                await self._events.emit(
                    task_id=task_id,
                    correlation_id=correlation_id,
                    state=machine.state.value,
                    kind="reasoning.action",
                    step=counter.steps,
                    action_kind=action.kind,
                    tool=action.tool,
                )

                # Phase 2: Capture action for trajectory
                trajectory_actions.append(
                    ActionRecord(
                        step=action.step,
                        kind=action.kind,
                        tool=action.tool,
                        operation=action.operation,
                        args=action.args,
                        final_text=action.final_text,
                    )
                )

                # Phase 3: Record tool selection decision trace
                if action.kind == "tool_call" and action.tool:
                    decision_traces.append(
                        DecisionTrace(
                            id=str(uuid.uuid4()),
                            task_id=task_id,
                            correlation_id=correlation_id,
                            ts=datetime.now(UTC),
                            decision_point=DecisionPoint.TOOL_SELECTION,
                            options_considered=(),
                            chosen_option=action.tool,
                            rationale="Model selected tool via OTAR reasoning",
                            context={
                                "step": action.step,
                                "operation": action.operation or "",
                            },
                            outcome=DecisionOutcome.UNKNOWN,
                            confidence=thought.confidence,
                        )
                    )

                if action.kind in ("final_answer", "ask_user"):
                    # ── Phase 1: Verify the answer before returning ──────────
                    machine.transition(TaskState.VALIDATING)
                    answer_text = action.final_text or ""
                    verification = await self._verifier.verify(
                        goal, answer_text, correlation_id, context, domain, tuple(evidence)
                    )

                    if not verification.passed and goal.can_replan() and self._replanner:
                        # Phase 3: Record verification decision as failure (leading to replan)
                        decision_traces.append(
                            DecisionTrace(
                                id=str(uuid.uuid4()),
                                task_id=task_id,
                                correlation_id=correlation_id,
                                ts=datetime.now(UTC),
                                decision_point=DecisionPoint.VERIFICATION,
                                options_considered=("accept", "replan"),
                                chosen_option="replan",
                                rationale=f"Verification failed (score {verification.score:.2f}). "
                                f"Reason: {verification.failure_reason or 'unspecified'}",
                                context={
                                    "score": verification.score if hasattr(verification, "score") else 0.0,
                                    "passed": verification.passed,
                                    "failure_reason": verification.failure_reason,
                                },
                                outcome=DecisionOutcome.FAILURE,
                                outcome_detail=(
                                    f"Score {verification.score:.2f}: "
                                    f"{verification.failure_reason or 'verification failed'}"
                                ),
                            )
                        )

                        # Also record a failure record for the verification failure
                        failure_records.append(
                            FailureRecordModel(
                                id=str(uuid.uuid4()),
                                task_id=task_id,
                                correlation_id=correlation_id,
                                ts=datetime.now(UTC),
                                category=FailureCategory.VERIFICATION_FAILED,
                                step=counter.steps,
                                component="verifier",
                                error_message=(
                                    f"Verification failed: {verification.failure_reason or 'verification failed'}"
                                ),
                                context={
                                    "score": verification.score if hasattr(verification, "score") else 0.0,
                                    "passed": verification.passed,
                                },
                                recovered=True,
                                recovery_method="replanning",
                                recovery_succeeded=True,
                            )
                        )

                        # Verification failed — try replanning
                        await self._events.emit(
                            task_id=task_id,
                            correlation_id=correlation_id,
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
                            f"Suggestions: {verification.suggested_next_action or 'none'}. "
                            f"Previous answer: {answer_text[:400]}"
                        )
                        goal = goal.with_replan()
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
                            task_id=task_id,
                            correlation_id=correlation_id,
                            state=machine.state.value,
                            kind="replan.finished",
                            replan_count=goal.replan_count,
                            new_goal=current_plan.goal,
                        )

                        # Phase 3: Record replanning decision trace for verification failure
                        decision_traces.append(
                            DecisionTrace(
                                id=str(uuid.uuid4()),
                                task_id=task_id,
                                correlation_id=correlation_id,
                                ts=datetime.now(UTC),
                                decision_point=DecisionPoint.REPLANNING,
                                options_considered=("accept_answer", "replan"),
                                chosen_option="replan",
                                rationale=f"Verification failed (score {verification.score:.2f}). "
                                f"Reason: {verification.failure_reason or 'unspecified'}",
                                context={
                                    "step": counter.steps,
                                    "verification_score": verification.score,
                                    "replan_count": goal.replan_count,
                                },
                                outcome=DecisionOutcome.SUBOPTIMAL,
                                outcome_detail="Replanned due to verification failure",
                            )
                        )

                        machine.transition(TaskState.REASONING)
                        continue

                    machine.transition(TaskState.COMPLETED)
                    trajectory_latency_ms = int((time.perf_counter() - trajectory_start) * 1000)

                    # Phase 3: Record verification decision
                    decision_traces.append(
                        DecisionTrace(
                            id=str(uuid.uuid4()),
                            task_id=task_id,
                            correlation_id=correlation_id,
                            ts=datetime.now(UTC),
                            decision_point=DecisionPoint.VERIFICATION,
                            options_considered=("verify", "replan"),
                            chosen_option="verify" if verification.passed else "replan",
                            rationale=verification.failure_reason or "No specific reason",
                            context={
                                "score": verification.score if hasattr(verification, "score") else 0.0,
                                "passed": verification.passed,
                            },
                            outcome=DecisionOutcome.SUCCESS if verification.passed else DecisionOutcome.FAILURE,
                            outcome_detail=(
                                verification.failure_reason or "Verification passed"
                                if verification.passed
                                else f"Score: {verification.score:.2f}"
                            ),
                        )
                    )

                    return TaskResult(
                        task_id=task_id,
                        ok=True,
                        answer=answer_text,
                        steps_taken=counter.steps,
                        replan_count=goal.replan_count,
                        verification_passed=verification.passed,
                        verification_score=verification.score,
                        # Phase 2: Trajectory data
                        actions=tuple(trajectory_actions),
                        observations=tuple(trajectory_observations),
                        decision_traces=tuple(decision_traces),
                        failure_records=tuple(failure_records),
                        latency_ms=trajectory_latency_ms,
                        tokens_used=counter.tokens,
                        model_calls=counter.steps,  # Rough approximation
                        tool_calls=counter.tool_calls,
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
                    task_id=task_id,
                    correlation_id=correlation_id,
                    state=machine.state.value,
                    kind="tool.requested",
                    tool=action.tool,
                    operation=action.operation,
                    args=action.args,
                )
                self._monitor.check_may_continue(token)
                machine.transition(TaskState.EXECUTING)
                await self._events.emit(
                    task_id=task_id,
                    correlation_id=correlation_id,
                    state=machine.state.value,
                    kind="tool.executing",
                    tool=action.tool,
                )

                async def _do_dispatch(a: Action = action) -> Observation:
                    return await self._dispatch.dispatch(a, correlation_id)

                obs = await self._retry.run(_do_dispatch, counter)
                await self._events.emit(
                    task_id=task_id,
                    correlation_id=correlation_id,
                    state=machine.state.value,
                    kind="tool.completed" if obs.ok else "tool.failed",
                    tool=action.tool,
                    ok=obs.ok,
                    error=obs.error,
                )
                machine.transition(TaskState.OBSERVING)
                await self._recorder.record_observation(correlation_id, obs)

                # Phase 2: Capture observation for trajectory
                trajectory_observations.append(
                    ObservationRecord(
                        step=obs.step,
                        ok=obs.ok,
                        content=str(obs.content)[:1000] if obs.content else None,  # Truncate for storage
                        error=obs.error,
                    )
                )

                # Phase 12: record bounded provenance for the verifier. The
                # summary is capped here so no verifier can be handed raw output.
                evidence.append(
                    Evidence(
                        source=action.tool or "unknown",
                        operation=action.operation or "",
                        ok=obs.ok,
                        summary=(str(obs.content) if obs.ok else str(obs.error or ""))[:400],
                    )
                )

                # Phase 3: Record safety tier classification for tool execution
                decision_traces.append(
                    DecisionTrace(
                        id=str(uuid.uuid4()),
                        task_id=task_id,
                        correlation_id=correlation_id,
                        ts=datetime.now(UTC),
                        decision_point=DecisionPoint.SAFETY_TIER,
                        options_considered=("auto", "notify", "confirm", "dangerous", "block"),
                        chosen_option="auto" if plan.risk.value == "low" else "confirm",
                        rationale="Tier assigned based on plan risk assessment",
                        context={
                            "tool": action.tool or "",
                            "operation": action.operation or "",
                            "plan_risk": plan.risk.value,
                            "obs_ok": obs.ok,
                        },
                        outcome=DecisionOutcome.SUCCESS if obs.ok else DecisionOutcome.FAILURE,
                        outcome_detail=(
                            "Tool executed successfully" if obs.ok else (obs.error or "Tool execution failed")
                        ),
                    )
                )

                # Phase 3: Record failure record if tool dispatch failed
                if not obs.ok:
                    failure_records.append(
                        FailureRecordModel(
                            id=str(uuid.uuid4()),
                            task_id=task_id,
                            correlation_id=correlation_id,
                            ts=datetime.now(UTC),
                            category=FailureCategory.TOOL_ERROR,
                            step=counter.steps,
                            component="tool_dispatcher",
                            error_message=obs.error or "unknown tool error",
                            context={
                                "tool": action.tool or "",
                                "operation": action.operation or "",
                                "args_keys": list(action.args.keys()),
                            },
                            recovered=bool(self._replanner),
                        )
                    )

                # Phase 0: Push observation into WorkingMemory
                if self._working:
                    self._working.add(
                        Episode(
                            correlation_id=correlation_id,
                            task_id=task_id,
                            ts=datetime.now(UTC),
                            kind=EpisodeKind.OBSERVATION,
                            role="tool",
                            content=str(obs.content)[:500] if obs.ok else str(obs.error)[:500],
                            tool=action.tool,
                            outcome="success" if obs.ok else "failure",
                            salience=0.5,
                        )
                    )

                # ── Phase 1: Replan on tool dispatch failure ─────────────────
                if not obs.ok and self._replanner and await self._replanner.should_replan(goal, obs):
                    await self._events.emit(
                        task_id=task_id,
                        correlation_id=correlation_id,
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
                    goal = goal.with_replan()
                    current_plan = await self._replanner.replan(
                        goal=goal,
                        original_plan=current_plan,
                        failure_context=failure_context,
                        correlation_id=correlation_id,
                        caps=caps,
                    )
                    history = history[-3:]
                    await self._events.emit(
                        task_id=task_id,
                        correlation_id=correlation_id,
                        state=machine.state.value,
                        kind="replan.finished",
                        replan_count=goal.replan_count,
                        new_goal=current_plan.goal,
                    )

                    # Phase 3: Record replanning decision trace
                    decision_traces.append(
                        DecisionTrace(
                            id=str(uuid.uuid4()),
                            task_id=task_id,
                            correlation_id=correlation_id,
                            ts=datetime.now(UTC),
                            decision_point=DecisionPoint.REPLANNING,
                            options_considered=("continue", "replan"),
                            chosen_option="replan",
                            rationale=f"Tool '{action.tool}' failed at step {counter.steps}, triggering replan",
                            context={
                                "step": counter.steps,
                                "tool": action.tool or "",
                                "operation": action.operation or "",
                                "replan_count": goal.replan_count,
                            },
                            outcome=DecisionOutcome.SUBOPTIMAL,
                            outcome_detail="Replanned due to tool failure",
                        )
                    )

                # OTAR Reflect step
                reflection = await self._reflection.reflect(action, obs, context)
                if reflection.learnings:
                    _log.info(
                        "reasoning.reflect",
                        event_type="orchestration",
                        correlation_id=correlation_id,
                        step=counter.steps,
                        learnings=reflection.learnings,
                        succeeded=reflection.succeeded,
                    )

                history.append((thought, obs))

                # Batch 7: durable progress — survive crashes mid-task.
                if self._checkpoints is not None:
                    try:
                        from atlas.orchestration.checkpoint import ExecutionCheckpoint

                        await self._checkpoints.save(
                            ExecutionCheckpoint(
                                task_id=str(task_id),
                                step=counter.steps,
                                goal=goal.model_dump(),
                                plan=current_plan.model_dump(),
                                history_summary=self._compactor.render(history[-4:])[:1500],
                                created_ts=datetime.now(UTC),
                            )
                        )
                    except Exception as exc:
                        _log.warning(
                            "checkpoint.save_failed", event_type="orchestration", task_id=str(task_id), error=repr(exc)
                        )

            except CancellationError as exc:
                machine.transition(TaskState.CANCELLING)
                machine.transition(TaskState.FAILED)
                trajectory_latency_ms = int((time.perf_counter() - trajectory_start) * 1000)
                if self._checkpoints is not None:
                    await self._checkpoints.prune(str(task_id))
                failure_records.append(
                    FailureRecordModel(
                        id=str(uuid.uuid4()),
                        task_id=task_id,
                        correlation_id=correlation_id,
                        ts=datetime.now(UTC),
                        category=FailureCategory.CANCELLATION,
                        step=counter.steps,
                        component="reasoning_loop",
                        error_message=str(exc),
                        context={"final_step": counter.steps},
                        recovered=False,
                    )
                )
                return TaskResult(
                    task_id=task_id,
                    ok=False,
                    error=str(exc),
                    steps_taken=counter.steps,
                    replan_count=goal.replan_count,
                    actions=tuple(trajectory_actions),
                    observations=tuple(trajectory_observations),
                    decision_traces=tuple(decision_traces),
                    failure_records=tuple(failure_records),
                    latency_ms=trajectory_latency_ms,
                    tokens_used=counter.tokens,
                    model_calls=counter.steps,
                    tool_calls=counter.tool_calls,
                )
            except OrchestrationError as exc:
                await self._events.emit(
                    task_id=task_id,
                    correlation_id=correlation_id,
                    state=machine.state.value,
                    kind="task.failed",
                    error=str(exc),
                )
                machine.transition(TaskState.FAILED)
                trajectory_latency_ms = int((time.perf_counter() - trajectory_start) * 1000)
                if self._checkpoints is not None:
                    await self._checkpoints.prune(str(task_id))
                failure_records.append(
                    FailureRecordModel(
                        id=str(uuid.uuid4()),
                        task_id=task_id,
                        correlation_id=correlation_id,
                        ts=datetime.now(UTC),
                        category=FailureCategory.UNKNOWN,
                        step=counter.steps,
                        component="reasoning_loop",
                        error_message=str(exc),
                        context={"error_type": type(exc).__name__, "final_step": counter.steps},
                        recovered=False,
                    )
                )
                return TaskResult(
                    task_id=task_id,
                    ok=False,
                    error=str(exc),
                    steps_taken=counter.steps,
                    replan_count=goal.replan_count,
                    actions=tuple(trajectory_actions),
                    observations=tuple(trajectory_observations),
                    decision_traces=tuple(decision_traces),
                    failure_records=tuple(failure_records),
                    latency_ms=trajectory_latency_ms,
                    tokens_used=counter.tokens,
                    model_calls=counter.steps,
                    tool_calls=counter.tool_calls,
                )

    async def _reason_once(
        self,
        task_id: TaskId,
        correlation_id: CorrelationId,
        plan: Plan,
        context: str,
        history: list[tuple[Thought, Observation | None]],
        counter: LimitCounter,
    ) -> tuple[Thought, Action, DecisionTrace | None]:
        prompt = self._prompts.build_step_prompt(
            context=context,
            goal=plan.goal,
            history=history,
            step=counter.steps,
        )
        started = time.perf_counter()
        resp = await with_timeout(
            self._gw.complete(
                ModelRequest(
                    correlation_id=correlation_id,
                    prompt=prompt,
                    required_capabilities=frozenset(
                        {
                            ModelCapability.REASONING,
                            ModelCapability.TOOL_CALLING,
                            ModelCapability.JSON_GENERATION,
                        }
                    ),
                    needs_deep_reasoning=(plan.confidence < 0.6),
                    stakes_tier=Tier.CONFIRM if plan.risk.value != "low" else Tier.AUTO,
                    max_tokens=6144,
                )
            ),
            seconds=self._model_timeout_s,
            what="model.complete",
        )
        model_latency_ms = int((time.perf_counter() - started) * 1000)
        counter.add_tokens(resp.cost.input_tokens + resp.cost.output_tokens)

        # Phase 3: Record model selection decision
        model_trace: DecisionTrace = DecisionTrace(
            id=str(uuid.uuid4()),
            task_id=task_id,
            correlation_id=correlation_id,
            ts=datetime.now(UTC),
            decision_point=DecisionPoint.MODEL_SELECTION,
            options_considered=(str(resp.model),),
            chosen_option=str(resp.model),
            rationale="Selected by ModelGateway under active profile constraints",
            context={
                "requires_deep_reasoning": plan.confidence < 0.6,
                "stakes_tier": "CONFIRM" if plan.risk.value != "low" else "AUTO",
                "required_capabilities": sorted(
                    c.name
                    for c in {
                        ModelCapability.REASONING,
                        ModelCapability.TOOL_CALLING,
                        ModelCapability.JSON_GENERATION,
                    }
                ),
            },
            outcome=DecisionOutcome.UNKNOWN,
            latency_ms=model_latency_ms,
            cost_usd=resp.cost.usd,
        )

        await self._events.emit(
            task_id=task_id,
            correlation_id=correlation_id,
            state="reasoning",
            kind="reasoning.step",
            latency_ms=model_latency_ms,
            step=counter.steps,
        )
        thought, action = self._parser.parse(resp.text, counter.steps)
        if hasattr(resp, "reasoning_details") and resp.reasoning_details:
            # Recreate thought with reasoning_details
            thought = Thought(
                step=thought.step,
                content=thought.content,
                confidence=thought.confidence,
                reasoning_details=resp.reasoning_details,
            )
        self._validator.validate(action)

        # Update the model selection trace outcome based on parsed action
        if action.kind == "final_answer":
            model_trace = model_trace.model_copy(
                update={
                    "outcome": DecisionOutcome.SUCCESS,
                    "outcome_detail": "Action resolved to final answer",
                }
            )
        elif action.kind == "tool_call":
            model_trace = model_trace.model_copy(
                update={
                    "outcome": DecisionOutcome.SUCCESS,
                    "outcome_detail": f"Action selected tool: {action.tool}",
                }
            )
        elif action.kind == "noop":
            model_trace = model_trace.model_copy(
                update={
                    "outcome": DecisionOutcome.SUBOPTIMAL,
                    "outcome_detail": "Model produced a noop action",
                }
            )

        return thought, action, model_trace
