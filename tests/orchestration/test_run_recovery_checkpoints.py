"""Write-ahead execution recovery over the real shared SQLite substrate."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import pytest

from atlas.infra.clock import SystemClock
from atlas.infra.db import Database
from atlas.infra.execution_lease import OwnershipLostError
from atlas.infra.ids import CorrelationId, UuidGenerator
from atlas.infra.types import ProviderToolCall
from atlas.orchestration.agent_engine.engine import AgentEngine
from atlas.orchestration.agent_engine.persistence import SqliteAgentRunStore
from atlas.orchestration.agent_engine.records import AgentRunRecord
from atlas.orchestration.agent_engine.service import AgentRunError, AgentRunService
from atlas.orchestration.limits import ExecutionLimits
from atlas.orchestration.research.persistence import SqliteResearchSessionStore
from atlas.orchestration.research.service import ResearchError, ResearchService
from atlas.orchestration.types import Action, Observation
from tests.orchestration.test_agent_run_service import CALC, SEARCH, FakeClock, FakeGateway, FakeRouter, _resp


@pytest.fixture
async def databases(tmp_path: Path) -> AsyncIterator[tuple[Database, Database]]:
    first, second = Database(tmp_path / "atlas.db"), Database(tmp_path / "atlas.db")
    await first.start()
    await second.start()
    try:
        yield first, second
    finally:
        await second.stop()
        await first.stop()


def agent(db: Database, dispatcher: object, gateway: object) -> AgentRunService:
    return AgentRunService(
        gateway=gateway,
        dispatcher=dispatcher,
        tool_router=FakeRouter([SEARCH, CALC]),
        store=SqliteAgentRunStore(db),
        ids=UuidGenerator(),
        clock=FakeClock(),
    )  # type: ignore[arg-type]


class WaitingDispatcher:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.calls: list[Action] = []

    async def dispatch(self, action: Action, correlation_id: CorrelationId) -> Observation:
        self.calls.append(action)
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


async def test_incremental_intents_and_observations_survive_cancellation(databases: tuple[Database, Database]) -> None:
    first, second = databases
    reader = SqliteAgentRunStore(second)
    second_entered = asyncio.Event()
    calls = (
        ProviderToolCall(id="read", name="search", arguments={"operation": "web"}, thought_signature="signature-1"),
        ProviderToolCall(id="write", name="calc", arguments={"operation": "add"}, thought_signature="signature-2"),
    )
    observed = []

    class Dispatcher:
        async def dispatch(self, action: Action, correlation_id: CorrelationId) -> Observation:
            active = (await reader.running_runs())[0]
            observed.append(active)
            if action.tool == "search":
                return Observation(step=0, ok=True, content={"saved": "first result"})
            second_entered.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    service = agent(first, Dispatcher(), FakeGateway([_resp("partial assistant", *calls)]))
    stub = await service.start_run_background("read then write")
    try:
        await asyncio.wait_for(second_entered.wait(), timeout=1)
        active = await reader.load_run(stub.run_id)
        assert active is not None and active.result is None and active.status == "running"
        assert observed[0].checkpoint.steps[0].tool_calls[0].state == "pending"
        assert active.checkpoint.model_calls == 1 and active.checkpoint.tokens_used == 15
        step = active.checkpoint.steps[0]
        assert step.assistant_text == "partial assistant"
        assert [(call.call_id, call.state) for call in step.tool_calls] == [("read", "completed"), ("write", "pending")]
        assert step.tool_calls[0].output == {"saved": "first result"}
        assert [call.thought_signature for call in step.tool_calls] == ["signature-1", "signature-2"]
    finally:
        await service.close()
    recovered = await reader.load_run(stub.run_id)
    assert recovered is not None and recovered.result is not None
    assert recovered.result.steps[0].tool_calls[0].output == {"saved": "first result"}
    assert recovered.result.steps[0].tool_calls[1].state == "unknown"
    assert "unknown" in recovered.result.error.lower()


@pytest.mark.parametrize("research", [False, True])
async def test_second_service_recovery_does_not_fail_live_owner(
    databases: tuple[Database, Database], research: bool
) -> None:
    first, second = databases
    dispatcher = WaitingDispatcher()
    if research:
        owner = ResearchService(
            dispatcher=dispatcher, store=SqliteResearchSessionStore(first), ids=UuidGenerator(), clock=FakeClock()
        )
        observer = ResearchService(
            dispatcher=WaitingDispatcher(),
            store=SqliteResearchSessionStore(second),
            ids=UuidGenerator(),
            clock=FakeClock(),
        )
        stub = await owner.start_session_background("evidence")
        key = stub.session_id
    else:
        owner = agent(first, dispatcher, FakeGateway([_resp("", ProviderToolCall(id="c", name="search"))]))
        observer = agent(second, WaitingDispatcher(), FakeGateway([]))
        stub = await owner.start_run_background("evidence")
        key = stub.run_id
    try:
        await asyncio.wait_for(dispatcher.started.wait(), timeout=1)
        assert await observer.recover_interrupted() == 0
        active = await (observer.get_session(key) if research else observer.get_run(key))
        assert active is not None and active.status == "running"
    finally:
        await observer.close()
        await owner.close()


async def test_checkpoint_store_failure_prevents_tool_dispatch(databases: tuple[Database, Database]) -> None:
    first, _ = databases

    class FailingCheckpointStore(SqliteAgentRunStore):
        async def save_owned(self, record: AgentRunRecord, owner_id: str, *, now: str) -> None:
            if record.status == "running" and record.checkpoint.steps:
                raise OSError("checkpoint unavailable")
            await super().save_owned(record, owner_id, now=now)

    dispatcher = WaitingDispatcher()
    service = agent(first, dispatcher, FakeGateway([_resp("", ProviderToolCall(id="c", name="search"))]))
    service._store = FailingCheckpointStore(first)
    try:
        result = await asyncio.wait_for(service.start_run("must persist before dispatch"), timeout=0.3)
        assert dispatcher.calls == []
        assert result.status == "error" and "checkpoint" in result.result.error
    finally:
        await service.close()


@pytest.mark.parametrize("research", [False, True])
async def test_closed_service_rejects_new_admission(databases: tuple[Database, Database], research: bool) -> None:
    first, _ = databases
    if research:
        service = ResearchService(
            dispatcher=WaitingDispatcher(),
            store=SqliteResearchSessionStore(first),
            ids=UuidGenerator(),
            clock=FakeClock(),
        )
    else:
        service = agent(first, WaitingDispatcher(), FakeGateway([]))
    await service.close()
    with pytest.raises(ResearchError if research else AgentRunError, match="closed"):
        if research:
            await service.start_session_background("too late")
        else:
            await service.start_run_background("too late")


async def test_legacy_recovery_explicitly_reports_unknown_outcomes(databases: tuple[Database, Database]) -> None:
    first, _ = databases
    await SqliteAgentRunStore(first).save_run(AgentRunRecord(run_id="legacy", request="possibly wrote files"))
    service = agent(first, WaitingDispatcher(), FakeGateway([]))
    assert await service.recover_interrupted() == 1
    recovered = await service.get_run("legacy")
    assert recovered is not None and "unknown" in recovered.result.error.lower()
    await service.close()


async def test_expired_crash_owner_is_settled_by_deferred_recovery(databases: tuple[Database, Database]) -> None:
    first, second = databases
    store = SqliteAgentRunStore(first)
    clock = SystemClock()
    now = clock.now()
    await store.leases.claim(
        "agent:crashed", "dead-owner", now=now.isoformat(), lease_until=(now + timedelta(seconds=0.05)).isoformat()
    )
    await store.save_run(AgentRunRecord(run_id="crashed"))
    observer = agent(second, WaitingDispatcher(), FakeGateway([]))
    observer._clock = clock
    observer._lease_renewal_s = 0.01
    try:
        assert await observer.recover_interrupted() == 0
        for _ in range(50):
            if (await observer.get_run("crashed")).status != "running":
                break
            await asyncio.sleep(0.01)
        recovered = await observer.get_run("crashed")
        assert recovered.status == "error" and "unknown" in recovered.result.error.lower()
    finally:
        await observer.close()


async def test_stale_owner_cannot_write_or_release_new_ownership(databases: tuple[Database, Database]) -> None:
    first, second = databases
    old, new = SqliteAgentRunStore(first), SqliteAgentRunStore(second)
    now = SystemClock().now()
    assert await old.leases.claim(
        "agent:fenced", "old", now=now.isoformat(), lease_until=(now + timedelta(seconds=1)).isoformat()
    )
    before = AgentRunRecord(run_id="fenced", request="original")
    await old.save_owned(before, "old", now=now.isoformat())
    later = (now + timedelta(seconds=2)).isoformat()
    assert await new.leases.claim(
        "agent:fenced", "new", now=later, lease_until=(now + timedelta(seconds=3)).isoformat()
    )
    with pytest.raises(OwnershipLostError):
        await old.save_owned(before.model_copy(update={"request": "stale overwrite"}), "old", now=later)
    await old.leases.release("agent:fenced", "old")
    assert await new.leases.is_live("agent:fenced", now=later)
    assert (await new.load_run("fenced")).request == "original"
    assert not await old.settle_orphan(before, before.model_copy(update={"status": "error"}), now=later)


async def test_ownership_ttl_does_not_limit_live_execution(databases: tuple[Database, Database]) -> None:
    first, second = databases
    dispatcher = WaitingDispatcher()
    service = agent(first, dispatcher, FakeGateway([_resp("", ProviderToolCall(id="c", name="search"))]))
    service._clock = SystemClock()
    service._lease_ttl_s, service._lease_renewal_s = 0.05, 0.01
    observer = agent(second, WaitingDispatcher(), FakeGateway([]))
    observer._clock = SystemClock()
    try:
        stub = await service.start_run_background("long operation")
        await dispatcher.started.wait()
        await asyncio.sleep(0.12)
        assert await observer.recover_interrupted() == 0
        assert (await observer.get_run(stub.run_id)).status == "running"
    finally:
        await observer.close()
        await service.close()


async def test_shutdown_is_bounded_when_dispatcher_delays_cancellation(databases: tuple[Database, Database]) -> None:
    first, _ = databases
    entered, release = asyncio.Event(), asyncio.Event()

    class SlowCancellationDispatcher:
        async def dispatch(self, action: Action, correlation_id: CorrelationId) -> Observation:
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await release.wait()
            return Observation(step=0, ok=True, content="late acknowledged outcome")

    service = agent(
        first, SlowCancellationDispatcher(), FakeGateway([_resp("", ProviderToolCall(id="c", name="search"))])
    )
    service._shutdown_timeout_s = 0.03
    stub = await service.start_run_background("operation")
    worker = service._inflight[stub.run_id]
    try:
        await entered.wait()
        await asyncio.wait_for(service.close(), timeout=0.2)
        pending = await service.get_run(stub.run_id)
        assert pending.status == "running" and pending.result is None
        assert pending.checkpoint.steps[0].tool_calls[0].state == "pending"
    finally:
        release.set()
        await asyncio.gather(worker, return_exceptions=True)
        await service.close()


async def test_renewal_loss_closes_admission_and_cancels_owned_worker(databases: tuple[Database, Database]) -> None:
    first, second = databases
    dispatcher = WaitingDispatcher()
    service = agent(first, dispatcher, FakeGateway([_resp("", ProviderToolCall(id="c", name="search"))]))
    service._clock = SystemClock()
    service._lease_ttl_s, service._lease_renewal_s = 0.3, 0.01
    stub = await service.start_run_background("owned operation")
    worker = service._inflight[stub.run_id]
    try:
        await dispatcher.started.wait()
        now = service._clock.now()
        await second.conn.execute(
            "UPDATE runtime_execution_leases SET lease_until=? WHERE resource_id=?",
            ((now - timedelta(seconds=1)).isoformat(), "agent:" + stub.run_id),
        )
        await second.conn.commit()
        leases = SqliteAgentRunStore(second).leases
        assert await leases.claim(
            "agent:" + stub.run_id,
            "new-owner",
            now=now.isoformat(),
            lease_until=(now + timedelta(seconds=1)).isoformat(),
        )
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(worker, timeout=0.3)
        with pytest.raises(AgentRunError, match="closed"):
            await service.start_run_background("cannot dispatch again")
        assert await leases.is_live("agent:" + stub.run_id, now=service._clock.now().isoformat())
        assert (await service.get_run(stub.run_id)).result is None
    finally:
        await service.close()


async def test_close_store_failure_preserves_checkpoint_for_next_recovery(databases: tuple[Database, Database]) -> None:
    first, second = databases

    class FailedShutdownStore(SqliteAgentRunStore):
        async def save_owned(self, record: AgentRunRecord, owner_id: str, *, now: str) -> None:
            if record.status == "error":
                raise OSError("terminal store unavailable")
            await super().save_owned(record, owner_id, now=now)

        async def settle_orphan(self, before: AgentRunRecord, after: AgentRunRecord, *, now: str) -> bool:
            raise OSError("recovery store unavailable")

    dispatcher = WaitingDispatcher()
    owner = agent(first, dispatcher, FakeGateway([_resp("", ProviderToolCall(id="c", name="search"))]))
    owner._store = FailedShutdownStore(first)
    stub = await owner.start_run_background("preserve intent")
    await dispatcher.started.wait()
    await asyncio.wait_for(owner.close(), timeout=0.3)
    observer = agent(second, WaitingDispatcher(), FakeGateway([]))
    assert await observer.recover_interrupted() == 1
    result = (await observer.get_run(stub.run_id)).result
    assert result.steps[0].tool_calls[0].state == "unknown"
    assert result.tool_calls == 1 and result.model_calls == 1
    await observer.close()


async def test_legacy_json_without_checkpoint_fields_can_be_settled(databases: tuple[Database, Database]) -> None:
    first, _ = databases
    record = AgentRunRecord(run_id="old-json", request="legacy work")
    await SqliteAgentRunStore(first).save_run(record)
    legacy = json.loads(record.model_dump_json())
    legacy.pop("checkpoint")
    await first.conn.execute("UPDATE agent_runs SET payload=? WHERE run_id=?", (json.dumps(legacy), record.run_id))
    await first.conn.commit()
    service = agent(first, WaitingDispatcher(), FakeGateway([]))
    try:
        assert await service.recover_interrupted() == 1
        recovered = await service.get_run(record.run_id)
        assert recovered.status == "error" and "unknown" in recovered.result.error.lower()
    finally:
        await service.close()


async def test_research_observation_survives_progress_event_failure(databases: tuple[Database, Database]) -> None:
    first, second = databases

    class FailingProgressStore(SqliteResearchSessionStore):
        async def append_owned_event(self, session_id, phase, payload, owner_id, *, ts):
            if phase == "sources_found":
                raise OSError("progress trace temporarily unavailable")
            return await super().append_owned_event(session_id, phase, payload, owner_id, ts=ts)

    class AnswerDispatcher:
        async def dispatch(self, action: Action, correlation_id: CorrelationId) -> Observation:
            return Observation(step=0, ok=True, content={"text": "saved grounded answer", "answered": True})

    service = ResearchService(
        dispatcher=AnswerDispatcher(), store=FailingProgressStore(first), ids=UuidGenerator(), clock=SystemClock()
    )
    try:
        record = await service.start_session("grounded research")
        durable = await SqliteResearchSessionStore(second).load_session(record.session_id)
        assert durable.answer.text == "saved grounded answer" and durable.status == "completed"
        assert record == durable
        assert (await service.session_events(record.session_id))[-1].phase == "completed"
    finally:
        await service.close()


async def test_token_limit_keeps_returned_assistant_turn_without_dispatch() -> None:
    call = ProviderToolCall(id="signed", name="search", thought_signature="preserve-on-limit")
    dispatcher = WaitingDispatcher()
    checkpoints = []

    async def save(checkpoint) -> None:
        checkpoints.append(checkpoint)

    result = await AgentEngine(
        gateway=FakeGateway([_resp("partial answer", call)]),
        dispatcher=dispatcher,
        limits=ExecutionLimits(max_tokens=1),
    ).run(correlation_id=CorrelationId("limit"), messages=[], tools=[SEARCH], checkpoint=save)
    assert result.stop_reason == "limit" and dispatcher.calls == []
    assert result.steps[0].assistant_tool_calls[0].thought_signature == "preserve-on-limit"
    assert checkpoints[-1].tokens_used == 15 and checkpoints[-1].model_calls == 1
