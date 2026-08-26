"""WebSocket routes for real-time event streaming.

WHY two endpoints: /ws/events for dashboard observability (all events) and
/ws/tasks/{id}/stream for CLI task tracking (scoped to one task). WHY historical
replay: clients reconnecting mid-task need to catch up; we replay buffered events
from the DB before switching to live stream.

TESTING:
- WebSocket: Use wscat or CLI commands (atlas task watch, atlas events stream)
- REST search: curl "http://localhost:8000/api/v1/events/search?limit=10"
- Frontend: Navigate to /events/search in browser
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict

from atlas.infra.bus import Event, MessageBus
from atlas.infra.db import Database
from atlas.infra.logging import get_logger
from atlas.interfaces.api.websocket import ConnectionManager

_log = get_logger("atlas.api.events")

router = APIRouter()


class EventStreamDependencies:
    """Shared dependencies for event streaming routes."""

    def __init__(self, manager: ConnectionManager, db: Database, bus: MessageBus | None = None) -> None:
        self.manager = manager
        self.db = db
        self.bus = bus


# Global singleton - will be set by create_app()
_deps: EventStreamDependencies | None = None


def set_dependencies(manager: ConnectionManager, db: Database, bus: MessageBus | None = None) -> None:
    """Initialize route dependencies (called from create_app)."""
    global _deps
    _deps = EventStreamDependencies(manager, db, bus)


@router.websocket("/ws/events")
async def global_event_stream(websocket: WebSocket) -> None:
    """
    Global event firehose - all events from all tasks.

    Used by: Web Dashboard for observability across all running tasks.

    Protocol:
    - Server sends JSON events as they occur
    - Client can send pong responses to ping keepalive
    - Connection stays open until client disconnects or error occurs
    """
    if _deps is None:
        await websocket.close(code=1011, reason="Server not initialized")
        return

    client_id = await _deps.manager.connect(websocket)
    _log.info("ws.global_stream_connected", event_type="websocket", client_id=client_id)

    try:
        # Keep connection alive - client can send pong or close
        while True:
            try:
                # Wait for client message (pong, close, etc)
                data = await websocket.receive_text()

                # Handle pong response
                if data == "pong":
                    continue

                # Handle client-initiated close
                if data == "close":
                    break

            except WebSocketDisconnect:
                break
            except Exception as exc:
                _log.error("ws.receive_error", event_type="websocket", client_id=client_id, error=str(exc))
                break

    finally:
        await _deps.manager.disconnect(client_id)
        _log.info("ws.global_stream_disconnected", event_type="websocket", client_id=client_id)


@router.websocket("/ws/tasks/{task_id}/stream")
async def task_scoped_stream(websocket: WebSocket, task_id: str) -> None:
    """
    Task-scoped event stream - only events for a specific task.

    Used by: CLI 'atlas task watch <id>' for focused task monitoring.

    Features:
    - Replays historical events from DB first (catch up on reconnect)
    - Then streams live events matching task_id
    - Automatically filters out events from other tasks

    Protocol:
    - Server sends historical events first (marked with "historical": true)
    - Then sends live events as they occur
    - Client can send pong responses to ping keepalive
    """
    if _deps is None:
        await websocket.close(code=1011, reason="Server not initialized")
        return

    client_id = await _deps.manager.connect(websocket)
    _log.info("ws.task_stream_connected", event_type="websocket", client_id=client_id, task_id=task_id)

    # Set filter for this client to only receive events for this task
    _deps.manager.set_filter(client_id, task_id=task_id)

    try:
        # 1. Send historical events first (replay from event_queue)
        historical_events = await _fetch_task_history(task_id)

        for event in historical_events:
            event["historical"] = True
            try:
                await websocket.send_json(event)
            except Exception as exc:
                _log.error("ws.send_historical_failed", event_type="websocket", client_id=client_id, error=str(exc))
                await _deps.manager.disconnect(client_id)
                return

        # Send replay complete marker
        await websocket.send_json(
            {"type": "replay_complete", "task_id": task_id, "historical_count": len(historical_events)}
        )

        _log.info(
            "ws.history_replayed",
            event_type="websocket",
            client_id=client_id,
            task_id=task_id,
            count=len(historical_events),
        )

        # 2. Now switch to live streaming (ConnectionManager handles this)
        # Keep connection alive
        while True:
            try:
                data = await websocket.receive_text()

                if data == "pong":
                    continue

                if data == "close":
                    break

            except WebSocketDisconnect:
                break
            except Exception as exc:
                _log.error("ws.receive_error", event_type="websocket", client_id=client_id, error=str(exc))
                break

    finally:
        await _deps.manager.disconnect(client_id)
        _log.info("ws.task_stream_disconnected", event_type="websocket", client_id=client_id, task_id=task_id)


async def _fetch_task_history(task_id: str) -> list[dict[str, Any]]:
    """
    Fetch historical events for a task from the new canonical events table.

    Returns events in chronological order (oldest first).
    """
    if _deps is None:
        return []

    try:
        cursor = await _deps.db.conn.execute(
            """
            SELECT type as topic, payload, occurred_at as created_ts
            FROM events
            WHERE causation_id = ?
            ORDER BY occurred_at ASC
            LIMIT 1000
            """,
            (task_id,),
        )
        rows = await cursor.fetchall()

        events = []
        for row in rows:
            import json

            event_data = json.loads(row["payload"])
            event_data["_topic"] = row["topic"]
            event_data["_timestamp"] = row["created_ts"]
            events.append(event_data)

        return events

    except Exception as exc:
        _log.error("ws.fetch_history_failed", event_type="websocket", task_id=task_id, error=str(exc))
        return []


@router.get("/ws/stats")
async def websocket_stats() -> dict[str, Any]:
    """
    Get WebSocket connection statistics (HTTP endpoint for monitoring).

    Returns:
        - total_connections: number of active WebSocket clients
        - clients: list of client IDs
    """
    if _deps is None:
        return {"error": "Server not initialized"}

    return _deps.manager.get_stats()


class EventSearchResult(BaseModel):
    """Search results with pagination metadata."""

    events: list[dict[str, Any]]
    total: int
    limit: int
    offset: int


async def _search_events(
    db: Database,
    task_id: str | None = None,
    topic: str | None = None,
    from_ts: str | None = None,
    to_ts: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> EventSearchResult:
    """
    Search events table with filters and pagination.

    Builds dynamic SQL query based on provided filters and returns
    paginated results with total count.
    """
    import json

    # Build WHERE clause dynamically
    conditions = []
    params = []

    if task_id:
        conditions.append("causation_id = ?")
        params.append(task_id)

    if topic:
        conditions.append("type LIKE ?")
        params.append(f"%{topic}%")

    if from_ts:
        conditions.append("occurred_at >= ?")
        params.append(from_ts)

    if to_ts:
        conditions.append("occurred_at <= ?")
        params.append(to_ts)

    where_clause = " AND ".join(conditions) if conditions else "1=1"

    # Count total matches
    count_query = f"SELECT COUNT(*) as total FROM events WHERE {where_clause}"
    cursor = await db.conn.execute(count_query, params)
    row = await cursor.fetchone()
    total = row["total"] if row else 0

    # Fetch paginated results
    query = f"""
        SELECT type as topic, payload, causation_id as task_id, correlation_id, occurred_at as created_ts
        FROM events
        WHERE {where_clause}
        ORDER BY occurred_at DESC
        LIMIT ? OFFSET ?
    """
    cursor = await db.conn.execute(query, [*params, limit, offset])
    rows = await cursor.fetchall()

    # Parse JSON payloads
    events = []
    for row in rows:
        event_data = json.loads(row["payload"])
        event_data["_topic"] = row["topic"]
        event_data["_task_id"] = row["task_id"]
        event_data["_correlation_id"] = row["correlation_id"]
        event_data["_timestamp"] = row["created_ts"]
        events.append(event_data)

    return EventSearchResult(events=events, total=total, limit=limit, offset=offset)


@router.get("/api/v1/events/search")
async def search_events(
    task_id: str | None = Query(default=None, description="Filter by task ID"),
    topic: str | None = Query(default=None, description="Event type filter (supports partial match)"),
    from_ts: str | None = Query(default=None, description="Start timestamp (ISO format)"),
    to_ts: str | None = Query(default=None, description="End timestamp (ISO format)"),
    limit: int = Query(default=100, le=1000, description="Max results per page"),
    offset: int = Query(default=0, ge=0, description="Pagination offset"),
) -> dict[str, Any]:
    """
    Search historical events with filters and pagination.

    Query the canonical events table with optional filters for forensic analysis,
    debugging, and system-wide event exploration.

    Examples:
        /search?task_id=abc123
        /search?topic=tool.completed&limit=50
        /search?from_ts=2024-01-01T00:00:00Z&to_ts=2024-01-31T23:59:59Z
        /search?task_id=abc&topic=tool&limit=10&offset=20
    """
    if _deps is None:
        return {"error": "Server not initialized"}

    _log.info("events.search", event_type="api", task_id=task_id, topic=topic, limit=limit, offset=offset)

    try:
        result = await _search_events(
            _deps.db, task_id=task_id, topic=topic, from_ts=from_ts, to_ts=to_ts, limit=limit, offset=offset
        )
        return result.model_dump()
    except Exception as exc:
        _log.error("events.search_failed", event_type="api", error=str(exc))
        return {"error": str(exc), "events": [], "total": 0, "limit": limit, "offset": offset}


class EmitRequest(BaseModel):
    topic: str
    payload: dict[str, Any]


@router.post("/api/v1/events/emit")
async def emit_event(req: EmitRequest) -> dict[str, Any]:
    """Emit a manual event to the MessageBus."""
    if _deps is None or _deps.bus is None:
        return {"error": "Server or bus not initialized"}

    class DynamicEvent(Event):
        model_config = ConfigDict(extra="allow", frozen=True)
        correlation_id: str = "manual-emit"

    try:
        event = DynamicEvent.model_validate(req.payload)
        await _deps.bus.publish(req.topic, event)
        return {"status": "ok", "topic": req.topic}
    except Exception as exc:
        _log.error("events.emit_failed", event_type="api", error=str(exc))
        return {"error": str(exc)}


@router.post("/api/v1/events/{event_id}/replay")
async def replay_event(event_id: str) -> dict[str, Any]:
    """Replay an historical event onto the MessageBus."""
    if _deps is None or _deps.bus is None:
        return {"error": "Server or bus not initialized"}

    try:
        cur = await _deps.db.conn.execute("SELECT type, payload FROM events WHERE id = ?", (event_id,))
        row = await cur.fetchone()
        if not row:
            return {"error": "Event not found"}

        topic = row["type"]
        import json

        payload_dict = json.loads(row["payload"])

        class DynamicEvent(Event):
            model_config = ConfigDict(extra="allow", frozen=True)
            correlation_id: str = "replay"

        event = DynamicEvent.model_validate(payload_dict)
        await _deps.bus.publish(topic, event)
        return {"status": "ok", "topic": topic, "replayed_id": event_id}
    except Exception as exc:
        _log.error("events.replay_failed", event_type="api", error=str(exc))
        return {"error": str(exc)}


@router.post("/api/v1/webhooks/{source}")
async def receive_webhook(source: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Canonical event ingestion for external webhooks (e.g., GitHub, Stripe)."""
    if _deps is None or _deps.bus is None:
        return {"error": "Server or bus not initialized"}

    topic = f"webhook.{source}"

    class WebhookEvent(Event):
        model_config = ConfigDict(extra="allow", frozen=True)
        correlation_id: str = f"webhook-{source}"

    try:
        event = WebhookEvent.model_validate(payload)
        await _deps.bus.publish(topic, event)
        return {"status": "ok", "topic": topic}
    except Exception as exc:
        _log.error("events.webhook_failed", event_type="api", source=source, error=str(exc))
        return {"error": str(exc)}
