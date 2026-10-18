"""Simultaneous retries must create and dispatch exactly one durable task."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from atlas.infra.ids import UuidGenerator
from atlas.interfaces.api.event_store import TaskEventStore
from atlas.interfaces.api.facade import DefaultAtlasControlPlane
from atlas.interfaces.api.schemas import CreateTaskRequest
from tests.fakes import FakeClock


async def test_concurrent_creations_share_one_task(memory_db: Any) -> None:
    dispatched: list[str] = []
    dispatch_started = asyncio.Event()

    async def run(inbound):
        dispatched.append(inbound.task_id)
        dispatch_started.set()
        return SimpleNamespace(ok=True, error=None, answer="done")

    atlas = SimpleNamespace(
        db=memory_db,
        ids=UuidGenerator(),
        clock=FakeClock(datetime.now(UTC)),
        orchestrator=SimpleNamespace(run=run),
    )
    plane = DefaultAtlasControlPlane(atlas, TaskEventStore(memory_db), "test")
    command = CreateTaskRequest(request="Inspect the project", idempotency_key="shared-concurrent-request")
    first, second = await asyncio.gather(plane.create_task(command), plane.create_task(command))
    assert first.id == second.id
    async with asyncio.timeout(2):
        await dispatch_started.wait()
    assert dispatched == [first.id]
    # Await background persistence before the fixture closes its SQLite connection.
    async with asyncio.timeout(2):
        while True:
            cursor = await memory_db.conn.execute("SELECT payload FROM tasks WHERE id=?", (first.id,))
            row = await cursor.fetchone()
            if row and '"answer": "done"' in row["payload"]:
                break
            await asyncio.sleep(0)
