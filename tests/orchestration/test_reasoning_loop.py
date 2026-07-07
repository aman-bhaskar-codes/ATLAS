from typing import Any

from atlas.infra.ids import CorrelationId, TaskId
from atlas.infra.types import ModelRequest, ModelResponse, ModelTarget, TokenCost
from atlas.orchestration.limits import ExecutionLimits
from atlas.orchestration.managers.cancellation import CancellationToken
from atlas.orchestration.managers.retry import RetryManager
from atlas.orchestration.monitor import ExecutionMonitor
from atlas.orchestration.parser import ResponseParser
from atlas.orchestration.prompt_builder import PromptBuilder
from atlas.orchestration.reasoning import ReasoningLoop
from atlas.orchestration.reflection import NoOpReflection
from atlas.orchestration.state import TaskState, TaskStateMachine
from atlas.orchestration.types import Observation, Plan
from atlas.orchestration.validator import OutputValidator


class FakeGateway:
    def __init__(self, scripted: list[str]) -> None:
        self._s = scripted
        self._i = 0
    async def complete(self, req: ModelRequest) -> ModelResponse:
        text = self._s[min(self._i, len(self._s) - 1)]
        self._i += 1
        return ModelResponse(text=text, target=ModelTarget.LOCAL_FAST, model="fake",
                             cost=TokenCost(input_tokens=5, output_tokens=5))


class FakeDispatcher:
    def __init__(self) -> None:
        self.calls = 0
    async def dispatch(self, action: Any, correlation_id: str) -> Observation:
        self.calls += 1
        return Observation(step=action.step, ok=True, content="tool ok")


class FakeRecorder:
    async def record_thought(self, *a: Any) -> None: pass
    async def record_action(self, *a: Any) -> None: pass
    async def record_observation(self, *a: Any) -> None: pass


class FakeEvents:
    async def emit(self, **k: Any) -> None: pass


class FakeKill:
    def is_active(self) -> bool: return False


def _make_machine() -> TaskStateMachine:
    """Pre-advance to PLANNING — the state the Orchestrator has the machine in
    when it hands off to the ReasoningLoop."""
    m = TaskStateMachine()
    m.transition(TaskState.READY)
    m.transition(TaskState.BUILDING_CONTEXT)
    m.transition(TaskState.PLANNING)
    return m


def _loop(gateway: Any, dispatcher: Any) -> ReasoningLoop:
    return ReasoningLoop(
        gateway=gateway, dispatcher=dispatcher, parser=ResponseParser(),
        validator=OutputValidator(), prompts=PromptBuilder(), recorder=FakeRecorder(),  # type: ignore[arg-type]
        monitor=ExecutionMonitor(FakeKill()),  # type: ignore[arg-type]
        retry=RetryManager(base_s=0, max_s=0),
        reflection=NoOpReflection(), events=FakeEvents(),  # type: ignore[arg-type]
        limits=ExecutionLimits(max_steps=5),
    )


async def test_tool_then_final() -> None:
    gw = FakeGateway([
        '{"thought":"use tool","confidence":0.9,"action":{"kind":"tool_call",'
        '"tool":"filesystem","operation":"read","args":{"path":"/x"}}}',
        '{"thought":"done","confidence":0.9,"action":{"kind":"final_answer",'
        '"final_text":"here it is"}}',
    ])
    disp = FakeDispatcher()
    result = await _loop(gw, disp).run(
        task_id=TaskId("t1"), correlation_id=CorrelationId("c"),
        plan=Plan(goal="read x"), context="ctx",
        machine=_make_machine(), token=CancellationToken(),
    )
    assert result.ok and result.answer == "here it is" and disp.calls == 1


async def test_max_steps_terminates_gracefully() -> None:
    gw = FakeGateway(['{"thought":"loop","confidence":0.5,"action":{"kind":"noop"}}'])
    m = _make_machine()
    result = await _loop(gw, FakeDispatcher()).run(
        task_id=TaskId("t"), correlation_id=CorrelationId("c"),
        plan=Plan(goal="g"), context="c", machine=m, token=CancellationToken(),
    )
    assert not result.ok and "max_steps" in (result.error or "")
    assert m.state == TaskState.FAILED


async def test_cancellation_stops_cleanly() -> None:
    gw = FakeGateway(['{"thought":"x","confidence":0.5,"action":{"kind":"noop"}}'])
    tok = CancellationToken()
    tok.cancel()
    result = await _loop(gw, FakeDispatcher()).run(
        task_id=TaskId("t"), correlation_id=CorrelationId("c"),
        plan=Plan(goal="g"), context="c", machine=_make_machine(), token=tok,
    )
    assert not result.ok and "cancel" in (result.error or "").lower()
