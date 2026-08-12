# WebSocket Event Streaming - Testing Guide

This guide explains how to test the Phase 1 Event Bus → WebSocket Bridge implementation.

## Prerequisites

Install `wscat` for WebSocket testing:
```bash
npm install -g wscat
```

## Start the ATLAS Server

```bash
cd /path/to/atlas
uv run atlas serve
```

The server should start on `http://localhost:8000`.

## Test 1: Global Event Stream

Connect to the global event firehose (all events from all tasks):

```bash
wscat -c ws://localhost:8000/ws/events
```

**Expected behavior:**
- Connection establishes successfully
- You receive periodic `ping` messages every 30 seconds:
  ```json
  {"type": "ping", "timestamp": 1234567890.123}
  ```
- Send `pong` to respond (keeps connection alive)
- Any events published by the system appear in real-time
- Send `close` to gracefully disconnect

## Test 2: Task-Scoped Event Stream

Connect to a task-specific stream:

```bash
wscat -c ws://localhost:8000/ws/tasks/test-task-123/stream
```

**Expected behavior:**
- Connection establishes successfully
- You immediately receive a `replay_complete` message:
  ```json
  {
    "type": "replay_complete",
    "task_id": "test-task-123",
    "historical_count": 0
  }
  ```
- If the task exists and had previous events, `historical_count` > 0
- Historical events are marked with `"historical": true`
- After replay, you receive live events for this task only

## Test 3: Connection Statistics

Check active WebSocket connections:

```bash
curl http://localhost:8000/ws/stats
```

**Expected response:**
```json
{
  "total_connections": 2,
  "clients": ["uuid-1", "uuid-2"]
}
```

## Test 4: End-to-End with Real Task

This demonstrates the full event flow from task creation to WebSocket delivery.

**Terminal 1** - Connect to task stream:
```bash
wscat -c ws://localhost:8000/ws/tasks/demo-task-456/stream
```

**Terminal 2** - Create a task:
```bash
curl -X POST http://localhost:8000/api/v1/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "goal": "list files in the current directory",
    "task_id": "demo-task-456"
  }'
```

**Terminal 1** should show:
1. `replay_complete` message (historical_count: 0)
2. `task.started` event
3. `context.building` event
4. `reasoning.thought` events as the orchestrator thinks
5. `tool.executing` when a tool is called
6. `tool.completed` after tool execution
7. Additional events as the task progresses

Example event:
```json
{
  "correlation_id": "abc-123",
  "task_id": "demo-task-456",
  "kind": "task.started",
  "state": "running",
  "metadata": {
    "summary": "Task started",
    "goal": "list files in the current directory"
  },
  "_topic": "orchestrator",
  "_timestamp": "2024-01-15T10:30:00.000Z"
}
```

## Test 5: Historical Replay

This tests that reconnecting clients can catch up on missed events.

**Step 1** - Create a task and let it run:
```bash
curl -X POST http://localhost:8000/api/v1/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "goal": "calculate 2 + 2",
    "task_id": "replay-test-789"
  }'
```

Wait a few seconds for the task to generate events.

**Step 2** - Connect to the task stream (after events occurred):
```bash
wscat -c ws://localhost:8000/ws/tasks/replay-test-789/stream
```

**Expected:**
- You receive `replay_complete` with `historical_count` > 0
- All previous events are sent with `"historical": true`
- Events are in chronological order (oldest first)
- After replay, you receive live events (without `historical` flag)

## Test 6: Multiple Clients

Open multiple terminals and connect to the same stream:

**Terminal 1:**
```bash
wscat -c ws://localhost:8000/ws/events
```

**Terminal 2:**
```bash
wscat -c ws://localhost:8000/ws/events
```

**Terminal 3:**
```bash
wscat -c ws://localhost:8000/ws/tasks/test-123/stream
```

**Terminal 4** - Check stats:
```bash
curl http://localhost:8000/ws/stats
```

Should show 3 active connections.

Create a task with `task_id: test-123`:
```bash
curl -X POST http://localhost:8000/api/v1/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "goal": "echo hello",
    "task_id": "test-123"
  }'
```

**Expected:**
- Terminals 1 & 2 (global streams) receive ALL events
- Terminal 3 (task-scoped) receives ONLY events for test-123

## Test 7: Event Types

Verify all event types are emitted:

```bash
wscat -c ws://localhost:8000/ws/events
```

Create a task that triggers multiple event types:
```bash
curl -X POST http://localhost:8000/api/v1/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "goal": "search the web for Python tutorials and save the results",
    "task_id": "event-types-test"
  }'
```

**Expected event types on the topic field:**
- `orchestrator`: task.started, context.building, reasoning.thought, reasoning.action
- `tool`: tool.executing, tool.completed, tool.failed
- `safety`: tier.classified, approval.requested, approval.denied, approval.resolved
- `memory`: memory.retrieved
- `planning`: (if planner is invoked)

## Test 8: Graceful Disconnection

```bash
wscat -c ws://localhost:8000/ws/events
```

**Option 1** - Client-initiated close:
```
> close
```

**Option 2** - Ctrl+C to force disconnect

**Expected:**
- Server detects disconnection
- Connection removed from stats
- No errors in server logs

Check stats after:
```bash
curl http://localhost:8000/ws/stats
```

Should show one fewer connection.

## Troubleshooting

### Connection refused
- Ensure ATLAS server is running (`uv run atlas serve`)
- Check server is on port 8000 (or adjust commands)

### No events appearing
- Verify events are being emitted by checking server logs
- Ensure MessageBus is started
- Check `event_log` table has entries:
  ```bash
  sqlite3 .atlas/data/atlas.db "SELECT COUNT(*) FROM event_log;"
  ```

### Pings not arriving
- EventBroadcaster keepalive task should be running
- Check server logs for broadcaster startup message
- Wait full 30 seconds for first ping

### Historical replay empty
- Events only stored after Task 1.9 implementation
- Pre-existing tasks won't have event_log entries
- Create a new task to test replay

## Success Criteria

✅ All tests above pass  
✅ No errors in server logs during testing  
✅ Unit tests pass: `uv run pytest tests/unit/ -v`  
✅ Integration tests pass: `uv run pytest tests/integration/test_websocket_events.py -v`  
✅ Multiple concurrent connections work without issues  
✅ Events flow from orchestrator → bus → log → WebSocket  
✅ Historical replay works correctly  
✅ Task filtering works (task-scoped streams only receive relevant events)

## Next Steps

After verification:
1. Frontend integration (Phase 2) can consume these WebSocket endpoints
2. CLI `atlas task watch <id>` can connect to `/ws/tasks/{id}/stream`
3. Dashboard can connect to `/ws/events` for real-time observability
