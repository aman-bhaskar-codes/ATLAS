"""A second process must not fail or erase a live governed task."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from atlas.infra.clock import SystemClock
from atlas.infra.db import Database
from atlas.infra.execution_lease import OwnershipLostError
from atlas.infra.execution_store import SQLiteCancellationStore, SQLiteExecutionStore
from atlas.infra.ids import TaskId, UuidGenerator
from atlas.infra.types import InboundEvent
from atlas.interfaces.api.event_store import TaskEventStore
from atlas.interfaces.api.facade import DefaultAtlasControlPlane
from atlas.interfaces.api.schemas import CreateTaskRequest
from atlas.orchestration.checkpoint import CheckpointStore, ExecutionCheckpoint, SQLiteCheckpointBackend
from atlas.orchestration.dag_executor import DagExecutor
from atlas.orchestration.orchestrator import Orchestrator
from atlas.orchestration.recovery import recover_interrupted_tasks
from atlas.orchestration.types import Observation, Plan, PlanStep
from tests.orchestration.agents.test_orchestrator_delegation import (
    FakeContextBuilder, FakeEvents, FakeReasoning, FakeRegistry, FakeUnderstanding,
)


@pytest.fixture
async def task_databases(tmp_path: Path) -> AsyncIterator[tuple[Database, Database]]:
    first, second = Database(tmp_path / "tasks.db"), Database(tmp_path / "tasks.db")
    await first.start()
    await second.start()
    try:
        yield first, second
    finally:
        await second.stop()
        await first.stop()


def checkpoints(db: Database) -> CheckpointStore:
    return CheckpointStore(SQLiteCheckpointBackend(db, UuidGenerator()), SystemClock())


def checkpoint(task_id: str) -> ExecutionCheckpoint:
    return ExecutionCheckpoint(task_id=task_id, step=1, goal={}, plan={}, history_summary="known progress",
                               created_ts=SystemClock().now())


class BlockingDagDispatcher:
    def __init__(self, store: CheckpointStore, task_id: str) -> None:
        self.store, self.task_id = store, task_id
        self.started, self.release = asyncio.Event(), asyncio.Event()
        self.calls = 0

    async def dispatch(self, action, correlation_id):
        self.calls += 1
        await self.store.save(checkpoint(self.task_id))
        self.started.set()
        await self.release.wait()
        return Observation(step=0, ok=True, content="observed")


class ConcretePlanner:
    async def plan(self, request, *args, **kwargs):
        return Plan(goal=request, steps=(PlanStep(index=0, intent="read", tool="fs", operation="read"),))


def orchestrator(db: Database, dispatcher: object) -> Orchestrator:
    return Orchestrator(ids=UuidGenerator(), clock=SystemClock(), execution_store=SQLiteExecutionStore(db),
        cancellation_store=SQLiteCancellationStore(db), understanding=FakeUnderstanding(), planner=ConcretePlanner(),
        context_builder=FakeContextBuilder(), reasoning=FakeReasoning(), registry=FakeRegistry(), events=FakeEvents(),
        dag_executor=DagExecutor(dispatcher))  # type: ignore[arg-type]


async def test_second_startup_preserves_live_task_and_checkpoint(task_databases: tuple[Database, Database]) -> None:
    first, second = task_databases
    dispatcher = BlockingDagDispatcher(checkpoints(first), "live")
    runtime = orchestrator(first, dispatcher)
    event = InboundEvent(task_id=TaskId("live"), correlation_id="live-cid", source="cli", content="read")
    worker = asyncio.create_task(runtime.run(event))
    try:
        await asyncio.wait_for(dispatcher.started.wait(), timeout=1)
        assert await recover_interrupted_tasks(second, checkpoints(second), SystemClock()) == []
        assert await checkpoints(second).latest("live") is not None
        cursor = await second.conn.execute("SELECT state FROM tasks WHERE id='live'")
        row = await cursor.fetchone()
        await cursor.close()
        await second.conn.commit()
        assert row is not None and row["state"] != "failed"
        dispatcher.release.set()
        assert (await asyncio.wait_for(worker, timeout=3.0)).ok
    finally:
        dispatcher.release.set()
        await asyncio.gather(worker, return_exceptions=True)


async def test_duplicate_live_task_is_rejected_before_second_dispatch(task_databases: tuple[Database, Database]) -> None:
    first, second = task_databases
    owner_dispatch = BlockingDagDispatcher(checkpoints(first), "same")
    other_dispatch = BlockingDagDispatcher(checkpoints(second), "same")
    owner, other = orchestrator(first, owner_dispatch), orchestrator(second, other_dispatch)
    event = InboundEvent(task_id=TaskId("same"), correlation_id="same-cid", source="cli", content="read")
    worker = asyncio.create_task(owner.run(event))
    try:
        await owner_dispatch.started.wait()
        with pytest.raises(OwnershipLostError):
            await asyncio.wait_for(other.run(event), timeout=0.15)
        assert other_dispatch.calls == 0
    finally:
        owner_dispatch.release.set()
        other_dispatch.release.set()
        await asyncio.gather(worker, return_exceptions=True)


async def test_api_admission_is_owned_before_background_worker_starts(task_databases: tuple[Database, Database]) -> None:
    first, second = task_databases
    gate = asyncio.Event()
    runtime = orchestrator(first, BlockingDagDispatcher(checkpoints(first), "unused"))
    actual_run = runtime.run

    async def delayed_run(event):
        await gate.wait()
        raise RuntimeError("controlled admission cleanup")

    runtime.run = delayed_run
    plane = DefaultAtlasControlPlane(SimpleNamespace(db=first, ids=UuidGenerator(), clock=SystemClock(),
        orchestrator=runtime), TaskEventStore(first), "test")
    try:
        response = await plane.create_task(CreateTaskRequest(request="queued", idempotency_key="ownership-window"))
        assert await recover_interrupted_tasks(second, checkpoints(second), SystemClock()) == []
        row = await (await second.conn.execute("SELECT state FROM tasks WHERE id=?", (response.id,))).fetchone()
        assert row["state"] == "created"
    finally:
        gate.set()
        runtime.run = actual_run
        await asyncio.sleep(0.02)
        close = getattr(runtime, "close", None)
        if close is not None:
            await close()
