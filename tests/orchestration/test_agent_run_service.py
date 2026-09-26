"""M2.1 — the agent-run use-case: AgentRunService over the governed engine.

Proves the service composes the M0.2 loop + M0.5 store into a persisted use-case:
a run seeds context, shortlists tools, drives the governed loop, and is stored;
get/list/continue work; a limit terminates gracefully into a stored record; and a
continuation rehydrates the prior run (its seed carries the rebuilt TOOL turns) and
links back via ``parent_run_id``. Real temp SQLite store; scripted fake gateway/
dispatcher/router — no network, no registry.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from atlas.infra.bus import MessageBus
from atlas.infra.db import Database
from atlas.infra.ids import CorrelationId, ExecutionId, TaskId
from atlas.infra.types import ProviderToolCall, ToolCallSpec
from atlas.intelligence.contracts import InferenceRequest, InferenceResponse, Role, Usage
from atlas.orchestration.agent_engine.event_reader import SqliteAgentEventReader
from atlas.orchestration.agent_engine.persistence import SqliteAgentRunStore
from atlas.orchestration.agent_engine.records import AgentRunRecord
from atlas.orchestration.agent_engine.service import AgentRunError, AgentRunNotReady, AgentRunService
from atlas.orchestration.events import EventPublisher
from atlas.orchestration.limits import ExecutionLimits
from atlas.orchestration.types import Action, Observation

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


class FakeRouter:
    """Structural stand-in for ToolRouter.shortlist_specs — honors max_tools."""

    def __init__(self, specs: Sequence[ToolCallSpec]) -> None:
        self._specs = list(specs)
        self.calls: list[tuple[str, int]] = []

    def shortlist_specs(
        self, intent: str = "", *, max_tools: int = 8, needs_side_effects: bool = False
    ) -> tuple[ToolCallSpec, ...]:
        self.calls.append((intent, max_tools))
        return tuple(self._specs[:max_tools])


class FakeIds:
    def __init__(self) -> None:
        self._n = 0

    def _next(self) -> str:
        self._n += 1
        return str(self._n)

    def task_id(self) -> TaskId:
        return TaskId(f"task-{self._next()}")

    def correlation_id(self) -> CorrelationId:
        return CorrelationId(f"cid-{self._next()}")

    def execution_id(self) -> ExecutionId:
        return ExecutionId(f"run-{self._next()}")


class FakeClock:
    def now(self) -> datetime:
        return datetime(2026, 9, 25, tzinfo=UTC)


class AdvancingClock:
    """Monotonic clock — each ``now()`` is one second later than the last.

    Lets a test distinguish the stub's ``created_ts`` from the terminal record's
    ``updated_ts`` and prove ``created_ts`` is preserved across the transition.
    """

    def __init__(self) -> None:
        self._ticks = 0

    def now(self) -> datetime:
        self._ticks += 1
        return datetime(2026, 9, 25, tzinfo=UTC) + timedelta(seconds=self._ticks)


def _resp(text: str = "", *calls: ProviderToolCall) -> InferenceResponse:
    return InferenceResponse(
        text=text,
        model_id="fake",
        provider="fake",
        usage=Usage(input_tokens=10, output_tokens=5),
        tool_calls=tuple(calls),
    )


def _service(
    db: Database,
    gateway: FakeGateway,
    router: FakeRouter,
    *,
    limits: ExecutionLimits | None = None,
    clock: object | None = None,
    publisher: EventPublisher | None = None,
    event_reader: object | None = None,
) -> AgentRunService:
    return AgentRunService(
        gateway=gateway,  # type: ignore[arg-type]
        dispatcher=FakeDispatcher({"search": Observation(step=0, ok=True, content="X is a letter")}),  # type: ignore[arg-type]
        tool_router=router,  # type: ignore[arg-type]
        store=SqliteAgentRunStore(db),
        ids=FakeIds(),  # type: ignore[arg-type]
        clock=clock or FakeClock(),  # type: ignore[arg-type]
        publisher=publisher,
        event_reader=event_reader,  # type: ignore[arg-type]
        limits=limits,
        system_prompt="you are ATLAS",
    )


# <<TESTS-MARKER>>
async def test_start_run_persists_and_returns_record(db: Database) -> None:
    gateway = FakeGateway(
        [
            _resp("", ProviderToolCall(id="c1", name="search", arguments={"operation": "web", "args": {"q": "X"}})),
            _resp("done: 3"),
        ]
    )
    router = FakeRouter([SEARCH, CALC])
    svc = _service(db, gateway, router)
    rec = await svc.start_run("search then answer", workspace_id="w1", session_id="s1")

    assert rec.result.ok and rec.result.final_text == "done: 3"
    assert rec.status == "finished"
    assert rec.tool_names == ("search", "calc")
    assert rec.workspace_id == "w1" and rec.session_id == "s1"
    assert rec.parent_run_id is None
    assert rec.task_id == rec.run_id  # the run's event stream keys on the run id
    # persisted, and the frozen record round-trips byte-for-byte
    assert await svc.get_run(rec.run_id) == rec
    # the model was seeded with the system prompt and offered the shortlisted tools
    first = gateway.requests[0]
    assert first.messages[0].role is Role.SYSTEM
    assert tuple(t.name for t in first.tools) == ("search", "calc")


async def test_start_run_records_limit_status(db: Database) -> None:
    gateway = FakeGateway([_resp("unreached")])
    svc = _service(db, gateway, FakeRouter([SEARCH]), limits=ExecutionLimits(max_steps=0))
    rec = await svc.start_run("do something")
    assert rec.status == "limit"
    assert not rec.result.ok
    assert rec.result.model_calls == 0  # limited before the first infer
    assert await svc.get_run(rec.run_id) == rec


# <<TESTS-MARKER-2>>
async def test_continue_run_links_parent_and_rehydrates(db: Database) -> None:
    gateway = FakeGateway(
        [
            _resp("", ProviderToolCall(id="c1", name="search", arguments={"operation": "web", "args": {"q": "X"}})),
            _resp("first answer"),
            _resp("continued answer"),
        ]
    )
    svc = _service(db, gateway, FakeRouter([SEARCH, CALC]))
    first = await svc.start_run("search then answer")
    cont = await svc.continue_run(first.run_id, "now multiply")

    assert cont.run_id != first.run_id
    assert cont.parent_run_id == first.run_id
    assert cont.request == "now multiply"
    assert cont.result.final_text == "continued answer"
    # the continuation's SEED is the rehydrated prior conversation (incl. the TOOL turn)
    assert any(m.role is Role.TOOL and m.tool_call_id == "c1" for m in cont.seed_messages)
    # and the model actually saw that history on the resumed turn
    assert any(m.role is Role.TOOL for m in gateway.requests[-1].messages)
    assert await svc.get_run(cont.run_id) == cont


async def test_continue_unknown_run_raises(db: Database) -> None:
    svc = _service(db, FakeGateway([]), FakeRouter([SEARCH]))
    with pytest.raises(AgentRunError):
        await svc.continue_run("nope", "hello")


# <<TESTS-MARKER-3>>
async def test_list_and_get_runs(db: Database) -> None:
    gateway = FakeGateway([_resp("a"), _resp("b")])
    svc = _service(db, gateway, FakeRouter([SEARCH]))
    r1 = await svc.start_run("first")
    r2 = await svc.start_run("second")
    runs = await svc.list_runs()
    assert {r.run_id for r in runs} == {r1.run_id, r2.run_id}
    assert await svc.get_run(r1.run_id) is not None
    assert await svc.get_run("missing") is None


async def test_max_tools_override(db: Database) -> None:
    gateway = FakeGateway([_resp("done")])
    router = FakeRouter([SEARCH, CALC])
    svc = _service(db, gateway, router)
    rec = await svc.start_run("only one tool please", max_tools=1)
    assert rec.tool_names == ("search",)
    assert router.calls[-1] == ("only one tool please", 1)


# <<TESTS-MARKER-4>> M2.2 Stage 1 — background execution + persisted "running" state.
async def test_start_run_background_returns_running_stub_then_converges(db: Database) -> None:
    gateway = FakeGateway(
        [
            _resp("", ProviderToolCall(id="c1", name="search", arguments={"operation": "web", "args": {"q": "X"}})),
            _resp("done: 3"),
        ]
    )
    svc = _service(db, gateway, FakeRouter([SEARCH, CALC]), clock=AdvancingClock())
    stub = await svc.start_run_background("search then answer", workspace_id="w1", session_id="s1")

    # The stub is returned immediately, addressable, and NOT yet resolved.
    assert stub.status == "running"
    assert stub.result is None
    assert stub.tool_names == ("search", "calc")
    assert stub.workspace_id == "w1" and stub.session_id == "s1"

    # Awaiting convergence yields the terminal record, overwriting the stub in place.
    final = await svc.wait_for(stub.run_id)
    assert final is not None
    assert final.run_id == stub.run_id
    assert final.status == "finished"
    assert final.result is not None and final.result.final_text == "done: 3"
    # created_ts is preserved from the stub; updated_ts advances to the terminal write.
    assert final.created_ts == stub.created_ts
    assert final.updated_ts > final.created_ts
    # the persisted record is the terminal one, and the in-flight registry is drained.
    assert await svc.get_run(stub.run_id) == final
    await asyncio.sleep(0)  # let the task's done-callback run
    assert stub.run_id not in svc._inflight


async def test_background_run_converges_to_error_on_unexpected_failure(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The convergence backstop: a failure OUTSIDE the governed loop still lands a
    terminal ERROR record, so a backgrounded run never strands in ``running``."""
    svc = _service(db, FakeGateway([]), FakeRouter([SEARCH]), clock=AdvancingClock())

    async def _boom(**_kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(svc._engine, "run", _boom)  # simulate a failure below the loop
    stub = await svc.start_run_background("will explode")
    assert stub.status == "running"

    final = await svc.wait_for(stub.run_id)
    assert final is not None
    assert final.status == "error"
    assert final.result is not None and not final.result.ok
    assert final.result.error is not None and "boom" in final.result.error
    assert final.created_ts == stub.created_ts  # stub's created_ts preserved into the ERROR record


async def test_continue_running_run_is_rejected(db: Database) -> None:
    """A run still in ``running`` has no result to rehydrate — continuing it is a
    conflict (``AgentRunNotReady``), distinct from an unknown run (``AgentRunError``)."""
    store = SqliteAgentRunStore(db)
    running = AgentRunRecord(
        run_id="run-live",
        task_id="run-live",
        request="in progress",
        result=None,
        status="running",
        created_ts="2026-09-25T00:00:00+00:00",
        updated_ts="2026-09-25T00:00:00+00:00",
    )
    await store.save_run(running)
    svc = _service(db, FakeGateway([]), FakeRouter([SEARCH]))
    with pytest.raises(AgentRunNotReady):
        await svc.continue_run("run-live", "keep going")


# <<TESTS-MARKER-5>> M2.2 Stage 2 — the live trace is durably readable via run_events.
async def test_run_events_surfaces_the_persisted_trace(db: Database) -> None:
    """End-to-end: a real run publishes its lifecycle/tool events through the bus,
    and ``run_events`` reads them back in order via the wired event reader — the
    exact path the SSE console streams."""
    gateway = FakeGateway(
        [
            _resp("", ProviderToolCall(id="c1", name="search", arguments={"operation": "web", "args": {"q": "X"}})),
            _resp("done"),
        ]
    )
    publisher = EventPublisher(MessageBus(db))
    svc = _service(db, gateway, FakeRouter([SEARCH]), publisher=publisher, event_reader=SqliteAgentEventReader(db))
    rec = await svc.start_run("search then answer")

    events = await svc.run_events(rec.run_id)
    kinds = [e.payload["kind"] for e in events]
    # bookended by the lifecycle, with the tool turn captured in between.
    assert kinds[0] == "agent.started" and kinds[-1] == "agent.completed"
    assert any(k.startswith("tool.") for k in kinds)
    # every event is keyed to this run and the sequence cursor is monotonic.
    assert all(e.causation_id == rec.run_id for e in events)
    seqs = [e.sequence for e in events]
    assert seqs == sorted(seqs)


async def test_run_events_empty_without_reader(db: Database) -> None:
    """No reader wired (a store-only build) → run_events degrades to empty, not error."""
    svc = _service(db, FakeGateway([_resp("hi")]), FakeRouter([SEARCH]))
    rec = await svc.start_run("say hi")
    assert await svc.run_events(rec.run_id) == ()
