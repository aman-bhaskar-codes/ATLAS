"""The agent execution engine (M0.2) — a governed native tool-calling loop.

WHY this exists: M0.1 taught every provider to *emit and parse* tool calls, but
nothing yet *drives* a multi-turn loop. This engine is that primitive, and only
that: model turn -> tool calls -> tool-result turns -> repeat until the model
answers or a limit trips. It deliberately duplicates none of the reasoning
fabric — it reuses the LIVE governed funnel (``ToolDispatcher.dispatch`` ->
``SafetyEngine.guard``), the LIVE seatbelts (``LimitCounter``), and the LIVE
event bus (``EventPublisher``). Higher layers (planner, verifier, replanner)
compose this loop; they are not reinvented here.

WHY structural Protocols instead of concrete types: the engine depends on two
seams — "something I can infer with" and "something I can dispatch through". Any
object with the right async method satisfies them, so tests inject fakes with no
network and no registry, and production passes the real gateway/dispatcher.
"""

from __future__ import annotations

from collections.abc import Sequence
from time import perf_counter
from typing import Any, Protocol

from atlas.infra.ids import CorrelationId
from atlas.infra.types import ToolCallSpec
from atlas.intelligence.capabilities import CapabilitySet
from atlas.intelligence.contracts import Constraints, InferenceRequest, InferenceResponse, Message, Role
from atlas.orchestration.agent_engine.records import (
    AgentEngineResult,
    AgentStep,
    StopReason,
    ToolCallRecord,
    serialize_tool_payload,
)
from atlas.orchestration.errors import OrchestrationTimeoutError, ReasoningError
from atlas.orchestration.events import EventPublisher
from atlas.orchestration.limits import ExecutionLimits, LimitCounter
from atlas.orchestration.types import Action, Observation


class SupportsInfer(Protocol):
    async def infer(self, request: InferenceRequest) -> InferenceResponse: ...


class SupportsDispatch(Protocol):
    async def dispatch(self, action: Action, correlation_id: CorrelationId) -> Observation: ...


def _decode_arguments(arguments: dict[str, object]) -> tuple[str | None, dict[str, Any]]:
    """Split a model's tool arguments into ATLAS's (operation, args).

    ToolRegistry advertises every tool with an ``{operation, args}`` schema, so
    a well-behaved call nests its real payload under ``args``. We tolerate a
    model that flattened them: anything outside ``operation``/``args`` is kept.
    """
    operation = arguments.get("operation")
    raw = arguments.get("args")
    if isinstance(raw, dict):
        args: dict[str, Any] = dict(raw)
    else:
        args = {k: v for k, v in arguments.items() if k not in ("operation", "args")}
    return (str(operation) if operation is not None else None), args


def _serialize_observation(obs: Observation) -> str:
    """Render an Observation as the TOOL turn's text content for the next model call.

    Delegates to the shared ``serialize_tool_payload`` so a persisted run rehydrated
    by ``agent_engine.context`` reproduces these TOOL turns exactly (M0.5).
    """
    return serialize_tool_payload(ok=obs.ok, output=obs.content, error=obs.error)


class AgentEngine:
    """Runs a bounded native tool-calling loop against a gateway + dispatcher."""

    def __init__(
        self,
        *,
        gateway: SupportsInfer,
        dispatcher: SupportsDispatch,
        publisher: EventPublisher | None = None,
        limits: ExecutionLimits | None = None,
    ) -> None:
        self._gateway = gateway
        self._dispatcher = dispatcher
        self._publisher = publisher
        self._limits = limits or ExecutionLimits()

    async def run(
        self,
        *,
        correlation_id: CorrelationId,
        messages: Sequence[Message],
        tools: Sequence[ToolCallSpec],
        task_id: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
        required_capabilities: CapabilitySet = frozenset(),
        constraints: Constraints | None = None,
    ) -> AgentEngineResult:
        counter = LimitCounter(self._limits)
        convo: list[Message] = list(messages)
        steps: list[AgentStep] = []
        constraints = constraints or Constraints()
        tid = task_id or str(correlation_id)
        model_calls = 0
        tool_calls = 0
        last_text = ""

        await self._emit(tid, correlation_id, state="executing", kind="agent.started")
        try:
            while True:
                counter.tick_step()
                request = InferenceRequest(
                    correlation_id=correlation_id,
                    messages=convo,
                    required_capabilities=required_capabilities,
                    constraints=constraints,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    task_id=task_id,
                    tools=tools,
                )
                resp = await self._gateway.infer(request)
                model_calls += 1
                counter.add_tokens(resp.usage.input_tokens + resp.usage.output_tokens)
                last_text = resp.text or last_text

                if not resp.tool_calls:
                    steps.append(AgentStep(index=len(steps), assistant_text=resp.text))
                    await self._emit(tid, correlation_id, state="completed", kind="agent.completed")
                    return AgentEngineResult(
                        ok=True,
                        stop_reason=StopReason.FINISHED,
                        final_text=resp.text,
                        steps=tuple(steps),
                        model_calls=model_calls,
                        tool_calls=tool_calls,
                        tokens_used=counter.tokens,
                    )

                # Replay the model's own tool choice back to it on the next turn.
                convo.append(Message(role=Role.ASSISTANT, content=resp.text, tool_calls=resp.tool_calls))
                records: list[ToolCallRecord] = []
                for call in resp.tool_calls:
                    counter.tick_tool()
                    tool_calls += 1
                    operation, args = _decode_arguments(call.arguments)
                    call_id = call.id or call.name
                    action = Action(step=len(steps), kind="tool_call", tool=call.name, operation=operation, args=args)
                    await self._emit_tool(tid, correlation_id, "tool.requested", call.name, operation, args)
                    t0 = perf_counter()
                    obs = await self._dispatcher.dispatch(action, correlation_id)
                    latency_ms = int((perf_counter() - t0) * 1000)
                    await self._emit_tool(
                        tid,
                        correlation_id,
                        "tool.completed" if obs.ok else "tool.failed",
                        call.name,
                        operation,
                        args,
                        result=obs.content,
                        error=obs.error,
                        latency_ms=latency_ms,
                    )
                    records.append(
                        ToolCallRecord(
                            call_id=call_id,
                            tool=call.name,
                            operation=operation,
                            args=args,
                            ok=obs.ok,
                            output=obs.content,
                            error=obs.error,
                            latency_ms=latency_ms,
                        )
                    )
                    convo.append(
                        Message(
                            role=Role.TOOL,
                            content=_serialize_observation(obs),
                            tool_call_id=call_id,
                            name=call.name,
                        )
                    )
                steps.append(AgentStep(index=len(steps), assistant_text=resp.text, tool_calls=tuple(records)))
        except (ReasoningError, OrchestrationTimeoutError) as exc:
            await self._emit(tid, correlation_id, state="limited", kind="agent.limited")
            return AgentEngineResult(
                ok=False,
                stop_reason=StopReason.LIMIT,
                final_text=last_text,
                steps=tuple(steps),
                model_calls=model_calls,
                tool_calls=tool_calls,
                tokens_used=counter.tokens,
                error=str(exc),
            )
        except Exception as exc:
            await self._emit(tid, correlation_id, state="error", kind="agent.error")
            return AgentEngineResult(
                ok=False,
                stop_reason=StopReason.ERROR,
                final_text=last_text,
                steps=tuple(steps),
                model_calls=model_calls,
                tool_calls=tool_calls,
                tokens_used=counter.tokens,
                error=str(exc),
            )

    async def _emit(self, task_id: str, correlation_id: str, *, state: str, kind: str) -> None:
        if self._publisher is not None:
            await self._publisher.emit(task_id=task_id, correlation_id=correlation_id, state=state, kind=kind)

    async def _emit_tool(
        self,
        task_id: str,
        correlation_id: str,
        kind: str,
        tool: str,
        operation: str | None,
        args: dict[str, Any],
        *,
        result: Any | None = None,
        error: str | None = None,
        latency_ms: int = 0,
    ) -> None:
        if self._publisher is not None:
            await self._publisher.emit_tool(
                task_id=task_id,
                correlation_id=correlation_id,
                kind=kind,
                tool=tool,
                operation=operation,
                args=args,
                result=result,
                error=error,
                latency_ms=latency_ms,
            )
