"""M0.2 — Agent Execution Engine loop.

Exit gate: a fake-provider 2-tool loop runs end-to-end. We drive the real
``AgentEngine`` with a scripted fake gateway (returns a queue of
InferenceResponses) and a fake dispatcher (maps tool name -> Observation), so no
network and no registry are touched. Also covered: an immediate answer, a
tool-error turn fed back to the model, and graceful limit enforcement.
"""

from __future__ import annotations

from collections import deque

from atlas.infra.ids import CorrelationId
from atlas.infra.types import ProviderToolCall, ToolCallSpec
from atlas.intelligence.contracts import InferenceRequest, InferenceResponse, Message, Role, Usage
from atlas.orchestration.agent_engine import AgentEngine, StopReason
from atlas.orchestration.limits import ExecutionLimits
from atlas.orchestration.types import Action, Observation

CID = CorrelationId("corr-1")
SEARCH = ToolCallSpec(name="search", description="search", parameters={"type": "object"})
CALC = ToolCallSpec(name="calc", description="calc", parameters={"type": "object"})


class FakeGateway:
    """Returns scripted responses in order; records each request it saw."""

    def __init__(self, responses: list[InferenceResponse]) -> None:
        self._queue: deque[InferenceResponse] = deque(responses)
        self.requests: list[InferenceRequest] = []

    async def infer(self, request: InferenceRequest) -> InferenceResponse:
        self.requests.append(request)
        return self._queue.popleft()


class FakeDispatcher:
    """Maps a tool name to a canned Observation; records dispatched actions."""

    def __init__(self, outcomes: dict[str, Observation]) -> None:
        self._outcomes = outcomes
        self.actions: list[Action] = []

    async def dispatch(self, action: Action, correlation_id: CorrelationId) -> Observation:
        self.actions.append(action)
        assert action.tool is not None
        return self._outcomes[action.tool]


def _resp(text: str = "", *calls: ProviderToolCall) -> InferenceResponse:
    return InferenceResponse(
        text=text,
        model_id="fake",
        provider="fake",
        usage=Usage(input_tokens=10, output_tokens=5),
        tool_calls=tuple(calls),
    )


async def test_two_tool_loop_e2e() -> None:
    gateway = FakeGateway(
        [
            _resp("", ProviderToolCall(id="c1", name="search", arguments={"operation": "web", "args": {"q": "X"}})),
            _resp("", ProviderToolCall(id="c2", name="calc", arguments={"operation": "add", "args": {"a": 1, "b": 2}})),
            _resp("done: 3"),  # no tool_calls -> finish
        ]
    )
    dispatcher = FakeDispatcher(
        {
            "search": Observation(step=0, ok=True, content="X is a letter"),
            "calc": Observation(step=1, ok=True, content=3),
        }
    )
    engine = AgentEngine(gateway=gateway, dispatcher=dispatcher)

    result = await engine.run(
        correlation_id=CID,
        messages=[Message(role=Role.USER, content="search then add")],
        tools=[SEARCH, CALC],
    )

    assert result.ok
    assert result.stop_reason is StopReason.FINISHED
    assert result.final_text == "done: 3"
    assert result.model_calls == 3
    assert result.tool_calls == 2
    assert result.tokens_used == 45  # 3 calls * (10 + 5)
    # two tool-driving steps + one final step
    assert len(result.steps) == 3
    assert [a.tool for a in dispatcher.actions] == ["search", "calc"]
    # the model's args were split into operation/args for the governed funnel
    assert dispatcher.actions[0].operation == "web"
    assert dispatcher.actions[0].args == {"q": "X"}
    # the second model call replayed the assistant tool_call + tool-result turn
    second = gateway.requests[1].messages
    assert any(m.role is Role.ASSISTANT and m.tool_calls for m in second)
    tool_turn = next(m for m in second if m.role is Role.TOOL)
    assert tool_turn.tool_call_id == "c1"
    assert tool_turn.name == "search"


async def test_immediate_answer_no_tools() -> None:
    gateway = FakeGateway([_resp("hello")])
    engine = AgentEngine(gateway=gateway, dispatcher=FakeDispatcher({}))

    result = await engine.run(
        correlation_id=CID,
        messages=[Message(role=Role.USER, content="hi")],
        tools=[SEARCH],
    )

    assert result.ok
    assert result.final_text == "hello"
    assert result.model_calls == 1
    assert result.tool_calls == 0
    assert len(result.steps) == 1


async def test_tool_error_is_fed_back_and_recovers() -> None:
    gateway = FakeGateway(
        [
            _resp("", ProviderToolCall(id="c1", name="search", arguments={"operation": "web"})),
            _resp("recovered"),
        ]
    )
    dispatcher = FakeDispatcher({"search": Observation(step=0, ok=False, error="denied by policy")})
    engine = AgentEngine(gateway=gateway, dispatcher=dispatcher)

    result = await engine.run(
        correlation_id=CID,
        messages=[Message(role=Role.USER, content="go")],
        tools=[SEARCH],
    )

    assert result.ok
    assert result.final_text == "recovered"
    # the failing observation is recorded and replayed as a TOOL turn
    assert result.steps[0].tool_calls[0].ok is False
    assert result.steps[0].tool_calls[0].error == "denied by policy"
    tool_turn = next(m for m in gateway.requests[1].messages if m.role is Role.TOOL)
    assert '"ok": false' in tool_turn.content


async def test_tool_call_limit_stops_gracefully() -> None:
    # Model keeps asking for a tool; max_tool_calls=1 must trip on the 2nd.
    call = ProviderToolCall(id="c", name="search", arguments={"operation": "web"})
    gateway = FakeGateway([_resp("", call), _resp("", call), _resp("never")])
    dispatcher = FakeDispatcher({"search": Observation(step=0, ok=True, content="ok")})
    engine = AgentEngine(
        gateway=gateway,
        dispatcher=dispatcher,
        limits=ExecutionLimits(max_tool_calls=1),
    )

    result = await engine.run(
        correlation_id=CID,
        messages=[Message(role=Role.USER, content="loop")],
        tools=[SEARCH],
    )

    assert result.ok is False
    assert result.stop_reason is StopReason.LIMIT
    assert result.tool_calls == 1
    assert result.error is not None and "max_tool_calls" in result.error


async def test_step_limit_stops_gracefully() -> None:
    call = ProviderToolCall(id="c", name="search", arguments={"operation": "web"})
    gateway = FakeGateway([_resp("", call) for _ in range(5)])
    dispatcher = FakeDispatcher({"search": Observation(step=0, ok=True, content="ok")})
    engine = AgentEngine(gateway=gateway, dispatcher=dispatcher, limits=ExecutionLimits(max_steps=2))

    result = await engine.run(
        correlation_id=CID,
        messages=[Message(role=Role.USER, content="loop")],
        tools=[SEARCH],
    )

    assert result.ok is False
    assert result.stop_reason is StopReason.LIMIT
    assert result.error is not None and "max_steps" in result.error
