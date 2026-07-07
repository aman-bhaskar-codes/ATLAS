"""Bounded ReAct reasoning loop — the heart of the runtime.

EXPLICIT BY DESIGN: each iteration drives the state machine through concrete
states and every external call is timed, retried (if recoverable), and audited.
The loop CANNOT run forever: limits raise typed errors the monitor turns into a
graceful FAILED. Tool actions go through the dispatcher (Safety Engine); final/
ask actions terminate. Reflection runs before consequential actions (Phase 4.5).
"""

from __future__ import annotations

import time

from atlas.infra.ids import CorrelationId, TaskId
from atlas.infra.logging import get_logger
from atlas.infra.types import ModelRequest, Tier
from atlas.intelligence.gateway import ModelGateway
from atlas.orchestration.dispatcher import ToolDispatcher
from atlas.orchestration.errors import CancellationError, OrchestrationError
from atlas.orchestration.events import EventPublisher
from atlas.orchestration.limits import ExecutionLimits, LimitCounter
from atlas.orchestration.managers.cancellation import CancellationToken
from atlas.orchestration.managers.retry import RetryManager
from atlas.orchestration.managers.timeout import with_timeout
from atlas.orchestration.monitor import ExecutionMonitor
from atlas.orchestration.parser import ResponseParser
from atlas.orchestration.prompt_builder import PromptBuilder
from atlas.orchestration.recorder import ExecutionRecorder
from atlas.orchestration.reflection import ReflectionHook
from atlas.orchestration.state import TaskState, TaskStateMachine
from atlas.orchestration.types import (
    Action,
    Observation,
    Plan,
    TaskResult,
    Thought,
)
from atlas.orchestration.validator import OutputValidator

_log = get_logger("atlas.orch.reasoning")


class ReasoningLoop:
    def __init__(
        self, *, gateway: ModelGateway, dispatcher: ToolDispatcher,
        parser: ResponseParser, validator: OutputValidator, prompts: PromptBuilder,
        recorder: ExecutionRecorder, monitor: ExecutionMonitor,
        retry: RetryManager, reflection: ReflectionHook, events: EventPublisher,
        limits: ExecutionLimits, model_timeout_s: float = 120.0,
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

    async def run(
        self, *, task_id: TaskId, correlation_id: CorrelationId, plan: Plan, context: str,
        machine: TaskStateMachine, token: CancellationToken,
    ) -> TaskResult:
        counter = LimitCounter(self._limits)
        history: list[tuple[Thought, Observation | None]] = []

        while True:
            try:
                self._monitor.check_may_continue(token)
                counter.tick_step()
                machine.transition(TaskState.REASONING)

                thought, action = await self._reason_once(
                    task_id, correlation_id, plan, context, history, counter,
                )
                await self._recorder.record_thought(correlation_id, thought)
                await self._recorder.record_action(correlation_id, action)

                if action.kind in ("final_answer", "ask_user"):
                    machine.transition(TaskState.VALIDATING)
                    machine.transition(TaskState.COMPLETED)
                    return TaskResult(task_id=task_id, ok=True,
                                      answer=action.final_text, steps_taken=counter.steps)
                if action.kind == "noop":
                    machine.transition(TaskState.VALIDATING)
                    history.append((thought, None))
                    continue

                # tool_call: reflect (Phase 4.5 seam) -> dispatch through Safety Engine
                action = await self._reflection.critique(action, context)
                counter.tick_tool()
                machine.transition(TaskState.WAITING_TOOL)
                self._monitor.check_may_continue(token)
                machine.transition(TaskState.EXECUTING)

                async def _do_dispatch(a: Action = action) -> Observation:
                    return await self._dispatch.dispatch(a, correlation_id)

                obs = await self._retry.run(_do_dispatch, counter)
                machine.transition(TaskState.OBSERVING)
                await self._recorder.record_observation(correlation_id, obs)
                history.append((thought, obs))

            except CancellationError as exc:
                machine.transition(TaskState.CANCELLING)
                machine.transition(TaskState.FAILED)
                return TaskResult(task_id=task_id, ok=False, error=str(exc),
                                  steps_taken=counter.steps)
            except OrchestrationError as exc:
                await self._events.emit(task_id=task_id, correlation_id=correlation_id,
                                        state=machine.state.value, kind="task.failed",
                                        error=str(exc))
                machine.transition(TaskState.FAILED)
                return TaskResult(task_id=task_id, ok=False, error=str(exc),
                                  steps_taken=counter.steps)

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
                needs_deep_reasoning=(plan.confidence < 0.6),
                stakes_tier=Tier.CONFIRM if plan.risk.value != "low" else Tier.AUTO,
                max_tokens=1024,
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
