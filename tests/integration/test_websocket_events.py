"""Integration tests for WebSocket event streaming.

These tests verify the WebSocket infrastructure components work correctly.
For end-to-end testing with a running server, see the manual testing section below.

Manual Testing Instructions:
============================

1. Start the ATLAS server:
   ```bash
   uv run atlas serve
   ```

2. Test global event stream with wscat:
   ```bash
   wscat -c ws://localhost:8000/ws/events
   ```
   You should see ping messages every 30 seconds. Send "pong" to respond.

3. Test task-scoped stream:
   ```bash
   wscat -c ws://localhost:8000/ws/tasks/test-123/stream
   ```
   You should see a replay_complete message, then live events for that task.

4. Test stats endpoint:
   ```bash
   curl http://localhost:8000/ws/stats
   ```
   Should show active connection count.

5. Test with actual task execution:
   ```bash
   # Terminal 1: Connect to task stream
   wscat -c ws://localhost:8000/ws/tasks/test-456/stream
   
   # Terminal 2: Create a task
   curl -X POST http://localhost:8000/api/v1/tasks \
     -H "Content-Type: application/json" \
     -d '{"goal": "list files in current directory", "task_id": "test-456"}'
   ```
   Terminal 1 should show events as the task executes.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from atlas.infra.bus import MessageBus
from atlas.infra.db import Database
from atlas.interfaces.api.websocket import ConnectionManager, EventBroadcaster
from atlas.orchestration.events import OrchestratorEvent


@pytest.mark.asyncio
async def test_connection_manager_basic():
    """Test ConnectionManager can track connections."""
    from unittest.mock import AsyncMock

    manager = ConnectionManager()

    # Mock WebSocket
    ws_mock = AsyncMock()
    ws_mock.accept = AsyncMock()

    # Connect
    client_id = await manager.connect(ws_mock)
    assert client_id is not None
    assert len(manager._active) == 1

    # Get stats
    stats = manager.get_stats()
    assert stats["total_connections"] == 1
    assert client_id in stats["clients"]

    # Disconnect
    await manager.disconnect(client_id)
    assert len(manager._active) == 0

    stats = manager.get_stats()
    assert stats["total_connections"] == 0


@pytest.mark.asyncio
async def test_connection_manager_filtering():
    """Test ConnectionManager filters events by task_id."""
    from unittest.mock import AsyncMock

    manager = ConnectionManager()

    ws1 = AsyncMock()
    ws1.accept = AsyncMock()
    ws1.send_json = AsyncMock()

    ws2 = AsyncMock()
    ws2.accept = AsyncMock()
    ws2.send_json = AsyncMock()

    # Connect two clients
    client1 = await manager.connect(ws1)
    client2 = await manager.connect(ws2)

    # Set filter on client1 for specific task
    manager.set_filter(client1, task_id="task-123")

    # Broadcast event for task-123
    event = OrchestratorEvent(
        correlation_id="corr-1", task_id="task-123", kind="task.started", state="running", metadata={}
    )

    await manager.broadcast(event)

    # Client1 (filtered) should receive it
    ws1.send_json.assert_called_once()

    # Client2 (no filter) should also receive it
    ws2.send_json.assert_called_once()

    await manager.disconnect(client1)
    await manager.disconnect(client2)


@pytest.mark.asyncio
async def test_event_broadcaster_subscribes():
    """Test EventBroadcaster subscribes to bus and broadcasts events."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = Path(tmp.name)

    try:
        db = Database(db_path)
        await db.start()

        bus = MessageBus(db)
        await bus.start()

        # Register event type
        bus.register_type("orchestrator", OrchestratorEvent)

        manager = ConnectionManager()
        broadcaster = EventBroadcaster(bus, manager)
        broadcaster.start()

        # Give it time to start
        await asyncio.sleep(0.1)

        # Verify broadcaster is active (has tasks)
        assert len(broadcaster._tasks) > 0

        # Cleanup
        await broadcaster.stop()
        await bus.close()
        await db.stop()

    finally:
        if db_path.exists():
            db_path.unlink()


@pytest.mark.asyncio
async def test_event_persistence_to_log():
    """Test that events are persisted to event_log table."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = Path(tmp.name)

    try:
        db = Database(db_path)
        await db.start()

        bus = MessageBus(db)
        await bus.start()

        # Register and publish event
        bus.register_type("orchestrator", OrchestratorEvent)

        event = OrchestratorEvent(
            correlation_id="test-corr-1",
            task_id="test-task-1",
            kind="task.started",
            state="running",
            metadata={"test": "data"},
        )

        await bus.publish("orchestrator", event)

        # Give bus time to process
        await asyncio.sleep(0.5)

        # Check events table
        cursor = await db.conn.execute("SELECT * FROM events WHERE causation_id = ?", ("test-task-1",))
        rows = await cursor.fetchall()

        assert len(rows) == 1
        assert rows[0]["type"] == "orchestrator"
        assert rows[0]["causation_id"] == "test-task-1"
        assert rows[0]["correlation_id"] == "test-corr-1"

        # Cleanup
        await bus.close()
        await db.stop()

    finally:
        if db_path.exists():
            db_path.unlink()


@pytest.mark.asyncio
async def test_historical_replay_query():
    """Test querying historical events for replay."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = Path(tmp.name)

    try:
        db = Database(db_path)
        await db.start()

        bus = MessageBus(db)
        await bus.start()

        bus.register_type("orchestrator", OrchestratorEvent)

        # Publish multiple events for the same task
        task_id = "replay-test-task"

        for i in range(3):
            event = OrchestratorEvent(
                correlation_id=f"corr-{i}",
                task_id=task_id,
                kind=f"test.event.{i}",
                state="running",
                metadata={"sequence": i},
            )
            await bus.publish("orchestrator", event)

        # Give bus time to process
        await asyncio.sleep(0.5)

        # Query historical events
        cursor = await db.conn.execute(
            """
            SELECT type, payload, occurred_at
            FROM events
            WHERE causation_id = ?
            ORDER BY occurred_at ASC
            """,
            (task_id,),
        )
        rows = await cursor.fetchall()

        assert len(rows) == 3

        # Verify order
        for i, row in enumerate(rows):
            data = json.loads(row["payload"])
            assert data["metadata"]["sequence"] == i

        # Cleanup
        await bus.close()
        await db.stop()

    finally:
        if db_path.exists():
            db_path.unlink()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
