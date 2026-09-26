"""M0.5 — agent-run context substrate: persistence + rehydration.

Proves the durable envelope round-trips on the shared SQLite substrate
(`SqliteAgentRunStore`), that the pure context helpers rebuild a run's exact
conversation, and — end to end — that a persisted run is REHYDRATED and
CONTINUED in a fresh engine turn. Uses a real temp `Database` and the same
scripted fake gateway/dispatcher as the M0.2 engine tests; no network, no
registry.
"""

from __future__ import annotations

from collections import deque
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from atlas.infra.db import Database
from atlas.infra.ids import CorrelationId
from atlas.infra.types import ProviderToolCall, ToolCallSpec
from atlas.intelligence.contracts import InferenceRequest, InferenceResponse, Message, Role, Usage
from atlas.orchestration.agent_engine import (
    AgentEngine,
    AgentEngineResult,
    AgentRunRecord,
    AgentStep,
    StopReason,
    ToolCallRecord,
    continue_messages,
    reconstruct_conversation,
    seed_messages,
)
from atlas.orchestration.agent_engine.persistence import SqliteAgentRunStore
from atlas.orchestration.types import Action, Observation

CID = CorrelationId("corr-1")
SEARCH = ToolCallSpec(name="search", description="search", parameters={"type": "object"})
CALC = ToolCallSpec(name="calc", description="calc", parameters={"type": "object"})


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "atlas.db")
    await database.start()
    try:
        yield database
    finally:
        await database.stop()


# ---- fakes (mirror tests/orchestration/test_agent_engine.py) -------------
class FakeGateway:
    def __init__(self, responses: list[InferenceResponse]) -> None:
        self._queue: deque[InferenceResponse] = deque(responses)
        self.requests: list[InferenceRequest] = []

    async def infer(self, request: InferenceRequest) -> InferenceResponse:
        self.requests.append(request)
        return self._queue.popleft()


class FakeDispatcher:
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


def _result(final: str = "done") -> AgentEngineResult:
    step = AgentStep(
        index=0,
        assistant_text="",
        tool_calls=(
            ToolCallRecord(call_id="c1", tool="search", operation="web", args={"q": "X"}, ok=True, output="X!"),
        ),
    )
    answer = AgentStep(index=1, assistant_text=final)
    return AgentEngineResult(
        ok=True, stop_reason=StopReason.FINISHED, final_text=final, steps=(step, answer), model_calls=2, tool_calls=1
    )


def _record(
    run_id: str,
    *,
    workspace_id: str | None = None,
    session_id: str | None = None,
    updated_ts: str = "2026-09-25T00:00:00Z",
    final: str = "done",
) -> AgentRunRecord:
    return AgentRunRecord(
        run_id=run_id,
        task_id="t1",
        correlation_id="corr-1",
        workspace_id=workspace_id,
        session_id=session_id,
        request="search then answer",
        tool_names=("search", "calc"),
        seed_messages=(Message(role=Role.USER, content="search then answer"),),
        result=_result(final),
        created_ts="2026-09-25T00:00:00Z",
        updated_ts=updated_ts,
    )


# ---- store CRUD ----------------------------------------------------------
async def test_save_load_round_trip(db: Database) -> None:
    store = SqliteAgentRunStore(db)
    rec = _record("r1", workspace_id="w1", session_id="s1")
    await store.save_run(rec)
    loaded = await store.load_run("r1")
    assert loaded == rec  # frozen models compare by value; JSON payload round-trips
    assert loaded is not None and loaded.status == "finished"


async def test_load_missing_returns_none(db: Database) -> None:
    assert await SqliteAgentRunStore(db).load_run("nope") is None


async def test_save_is_idempotent_upsert(db: Database) -> None:
    store = SqliteAgentRunStore(db)
    await store.save_run(_record("r1", final="first"))
    await store.save_run(_record("r1", final="second", updated_ts="2026-09-25T01:00:00Z"))
    runs = await store.list_runs()
    assert len(runs) == 1
    assert runs[0].result.final_text == "second"


async def test_list_runs_orders_by_updated_desc_and_filters_workspace(db: Database) -> None:
    store = SqliteAgentRunStore(db)
    await store.save_run(_record("old", workspace_id="w1", updated_ts="2026-09-25T00:00:00Z"))
    await store.save_run(_record("new", workspace_id="w1", updated_ts="2026-09-25T02:00:00Z"))
    await store.save_run(_record("other", workspace_id="w2", updated_ts="2026-09-25T03:00:00Z"))
    all_runs = await store.list_runs()
    assert [r.run_id for r in all_runs] == ["other", "new", "old"]  # updated_ts DESC
    w1 = await store.list_runs(workspace_id="w1")
    assert [r.run_id for r in w1] == ["new", "old"]
    assert len(await store.list_runs(limit=1)) == 1


async def test_runs_for_session(db: Database) -> None:
    store = SqliteAgentRunStore(db)
    await store.save_run(_record("a", session_id="s1", updated_ts="2026-09-25T00:00:00Z"))
    await store.save_run(_record("b", session_id="s1", updated_ts="2026-09-25T01:00:00Z"))
    await store.save_run(_record("c", session_id="s2"))
    got = await store.runs_for_session("s1")
    assert [r.run_id for r in got] == ["b", "a"]


async def test_delete_run(db: Database) -> None:
    store = SqliteAgentRunStore(db)
    await store.save_run(_record("r1"))
    await store.delete_run("r1")
    assert await store.load_run("r1") is None


# ---- pure context helpers ------------------------------------------------
def test_seed_messages_shape() -> None:
    msgs = seed_messages("SYS", "hello", history=(Message(role=Role.ASSISTANT, content="prev"),))
    assert [m.role for m in msgs] == [Role.SYSTEM, Role.ASSISTANT, Role.USER]
    assert msgs[0].content == "SYS" and msgs[-1].content == "hello"


def test_seed_messages_skips_empty_system() -> None:
    msgs = seed_messages("", "hello")
    assert [m.role for m in msgs] == [Role.USER]


def test_reconstruct_conversation_is_faithful() -> None:
    seed = [Message(role=Role.USER, content="go")]
    convo = reconstruct_conversation(seed, _result("done: 3"))
    # seed + [ASSISTANT(tool_calls), TOOL, ASSISTANT(final)]
    assert len(convo) == 4
    asst = convo[1]
    assert asst.role is Role.ASSISTANT and asst.tool_calls[0].name == "search"
    assert asst.tool_calls[0].arguments == {"operation": "web", "args": {"q": "X"}}
    tool = convo[2]
    assert tool.role is Role.TOOL and tool.tool_call_id == "c1" and tool.name == "search"
    assert '"ok": true' in tool.content and "X!" in tool.content
    assert convo[3].role is Role.ASSISTANT and convo[3].content == "done: 3" and convo[3].tool_calls == ()


def test_continue_messages_appends_user_turn() -> None:
    seed = [Message(role=Role.USER, content="go")]
    convo = continue_messages(seed, _result(), "and then?")
    assert convo[-1].role is Role.USER and convo[-1].content == "and then?"


# ---- end to end: run -> persist -> load -> continue ----------------------
async def test_run_persist_reload_and_continue(db: Database) -> None:
    seed = seed_messages("you are ATLAS", "search then add")
    gateway = FakeGateway(
        [
            _resp("", ProviderToolCall(id="c1", name="search", arguments={"operation": "web", "args": {"q": "X"}})),
            _resp("done: 3"),
        ]
    )
    dispatcher = FakeDispatcher({"search": Observation(step=0, ok=True, content="X is a letter")})
    engine = AgentEngine(gateway=gateway, dispatcher=dispatcher)
    result = await engine.run(correlation_id=CID, messages=seed, tools=[SEARCH, CALC])
    assert result.ok and result.final_text == "done: 3"

    store = SqliteAgentRunStore(db)
    rec = AgentRunRecord(
        run_id="run-1",
        task_id=str(CID),
        correlation_id=str(CID),
        request="search then add",
        seed_messages=tuple(seed),
        result=result,
        created_ts="2026-09-25T00:00:00Z",
        updated_ts="2026-09-25T00:00:00Z",
    )
    await store.save_run(rec)
    loaded = await store.load_run("run-1")
    assert loaded is not None and loaded == rec

    # Resume in a fresh engine (as a later process would): rehydrate + new turn.
    resume = continue_messages(loaded.seed_messages, loaded.result, "now multiply")
    assert any(m.role is Role.TOOL and m.tool_call_id == "c1" for m in resume)
    gateway2 = FakeGateway([_resp("multiplied")])
    engine2 = AgentEngine(gateway=gateway2, dispatcher=FakeDispatcher({}))
    result2 = await engine2.run(correlation_id=CID, messages=resume, tools=[SEARCH, CALC])
    assert result2.ok and result2.final_text == "multiplied"
    # the model saw the full rehydrated history on its first resumed turn
    assert any(m.role is Role.TOOL for m in gateway2.requests[0].messages)
