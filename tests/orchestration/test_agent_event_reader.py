"""M2.2 Stage 2 — the live-console trace reader: SqliteAgentEventReader.

Proves the reader projects one run's durable event stream out of the canonical
``events`` table exactly as the SSE console needs it: every lifecycle/tool event the
loop published under ``causation_id = run_id`` comes back in ``rowid`` order with a
monotonic ``sequence`` cursor; a second run's events are excluded; ``after_sequence``
resumes past a cursor; and ``limit`` bounds a batch. Real temp SQLite, real bus
publisher — no engine, no network.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from atlas.infra.bus import MessageBus
from atlas.infra.db import Database
from atlas.orchestration.agent_engine.event_reader import SqliteAgentEventReader
from atlas.orchestration.events import EventPublisher


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "atlas.db")
    await database.start()
    try:
        yield database
    finally:
        await database.stop()


async def _seed_run(publisher: EventPublisher, run_id: str) -> None:
    """Publish a minimal but representative run trace (persisted synchronously)."""
    cid = f"cid-{run_id}"
    await publisher.emit(task_id=run_id, correlation_id=cid, state="executing", kind="agent.started")
    await publisher.emit_tool(task_id=run_id, correlation_id=cid, kind="tool.requested", tool="search")
    await publisher.emit_tool(task_id=run_id, correlation_id=cid, kind="tool.completed", tool="search")
    await publisher.emit(task_id=run_id, correlation_id=cid, state="completed", kind="agent.completed")


async def test_reads_full_trace_in_order(db: Database) -> None:
    publisher = EventPublisher(MessageBus(db))
    await _seed_run(publisher, "run-1")
    reader = SqliteAgentEventReader(db)

    events = await reader.events_for_run("run-1")
    assert [e.payload["kind"] for e in events] == [
        "agent.started",
        "tool.requested",
        "tool.completed",
        "agent.completed",
    ]
    # sequence is the rowid cursor — strictly increasing, and the topic is the bus type.
    seqs = [e.sequence for e in events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    assert [e.type for e in events] == ["orchestrator", "tool", "tool", "orchestrator"]
    assert all(e.causation_id == "run-1" for e in events)


async def test_filters_by_run_id(db: Database) -> None:
    publisher = EventPublisher(MessageBus(db))
    await _seed_run(publisher, "run-a")
    await _seed_run(publisher, "run-b")
    reader = SqliteAgentEventReader(db)

    a = await reader.events_for_run("run-a")
    b = await reader.events_for_run("run-b")
    assert len(a) == 4 and len(b) == 4
    assert all(e.causation_id == "run-a" for e in a)
    assert all(e.causation_id == "run-b" for e in b)


async def test_after_sequence_resumes_past_cursor(db: Database) -> None:
    publisher = EventPublisher(MessageBus(db))
    await _seed_run(publisher, "run-1")
    reader = SqliteAgentEventReader(db)

    everything = await reader.events_for_run("run-1")
    after_first = await reader.events_for_run("run-1", after_sequence=everything[0].sequence)
    assert [e.sequence for e in after_first] == [e.sequence for e in everything[1:]]
    # resuming from the last cursor yields nothing more.
    assert await reader.events_for_run("run-1", after_sequence=everything[-1].sequence) == ()


async def test_limit_bounds_the_batch(db: Database) -> None:
    publisher = EventPublisher(MessageBus(db))
    await _seed_run(publisher, "run-1")
    reader = SqliteAgentEventReader(db)

    first_two = await reader.events_for_run("run-1", limit=2)
    assert len(first_two) == 2
    assert [e.payload["kind"] for e in first_two] == ["agent.started", "tool.requested"]


async def test_unknown_run_is_empty(db: Database) -> None:
    reader = SqliteAgentEventReader(db)
    assert await reader.events_for_run("nope") == ()
