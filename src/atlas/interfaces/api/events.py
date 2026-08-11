"""Server-Sent Events streaming for the Live Run Console.

Protocol per Phase Two spec:
1. Client opens stream for a task_id
2. Server sends the initial snapshot (all existing events) immediately
3. Server sends new events as they arrive from the orchestrator bus
4. Each event includes an id: field = sequence for client gap detection
5. On reconnect with Last-Event-ID header, server resumes from that sequence
6. When task reaches terminal state, a final event is sent and stream closes

SSE format per spec:
    id: {sequence}
    event: task_event
    data: {JSON}
    
The SSE queue mechanism:
- The lifespan subscriber (_on_orchestrator_event_sse) pushes task_id into
  per-task queues whenever a new event arrives.
- The SSE handler wakes, re-reads the latest events from the store, and
  streams any unseen events to the client.
- This avoids serializing the full event on the bus and lets the store be
  the single source of truth.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from atlas.interfaces.api.dependencies import get_control_plane
from atlas.interfaces.api.facade import AtlasControlPlane

router = APIRouter(tags=["events"])

_TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})


async def _event_generator(
    task_id: str,
    request: Request,
    control_plane: AtlasControlPlane,
    start_after_sequence: int | None,
) -> AsyncGenerator[str]:
    """Stream SSE events for task_id, resuming from start_after_sequence if provided."""

    sse_queues: dict[str, list[asyncio.Queue[str | None]]] = request.app.state.sse_queues
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    # Register this client's queue
    if task_id not in sse_queues:
        sse_queues[task_id] = []
    sse_queues[task_id].append(queue)

    last_sent_seq = start_after_sequence or 0

    try:
        # 1. Send connection event
        yield f"event: connected\ndata: {json.dumps({'status': 'connected', 'task_id': task_id})}\n\n"

        # 2. Send initial snapshot — all events after the client's last cursor
        initial_events = await control_plane.task_events(task_id, after_sequence=start_after_sequence)
        for event in initial_events:
            if await request.is_disconnected():
                return
            yield f"id: {event.sequence}\nevent: task_event\ndata: {event.model_dump_json()}\n\n"
            last_sent_seq = max(last_sent_seq, event.sequence)

        # Check if task is already terminal — close the stream after initial sync
        task = await control_plane.get_task(task_id)
        if task.state in _TERMINAL_STATES:
            yield f"event: stream_closed\ndata: {json.dumps({'reason': 'task_terminal', 'state': task.state})}\n\n"
            return

        # 3. Stream new events as they arrive
        while True:
            if await request.is_disconnected():
                return

            try:
                # Wait for a signal that new events are available for this task
                # Use a timeout to also periodically check for disconnect
                await asyncio.wait_for(queue.get(), timeout=15.0)
            except TimeoutError:
                # Heartbeat to keep the connection alive
                yield f"event: heartbeat\ndata: {json.dumps({'last_sequence': last_sent_seq})}\n\n"
                continue

            if await request.is_disconnected():
                return

            # Fetch events from the store since the last sent sequence
            new_events = await control_plane.task_events(task_id, after_sequence=last_sent_seq)
            for event in new_events:
                if await request.is_disconnected():
                    return
                yield f"id: {event.sequence}\nevent: task_event\ndata: {event.model_dump_json()}\n\n"
                last_sent_seq = max(last_sent_seq, event.sequence)

            # Check if task reached terminal state — close cleanly
            task = await control_plane.get_task(task_id)
            if task.state in _TERMINAL_STATES:
                yield f"event: stream_closed\ndata: {json.dumps({'reason': 'task_terminal', 'state': task.state})}\n\n"
                return

    finally:
        # Unregister queue on disconnect
        task_queues = sse_queues.get(task_id, [])
        if queue in task_queues:
            task_queues.remove(queue)
        if not task_queues:
            sse_queues.pop(task_id, None)


@router.get("/tasks/{task_id}/events/stream")
async def stream_task_events(
    task_id: str,
    request: Request,
    control_plane: AtlasControlPlane = Depends(get_control_plane),
) -> StreamingResponse:
    """Stream task events via SSE.

    Supports resume via the Last-Event-ID header (sequence number).
    The client should reconnect with Last-Event-ID after a disconnect.
    """
    # Support cursor-based resync: Last-Event-ID is the last known sequence
    last_event_id = request.headers.get("Last-Event-ID")
    start_after: int | None = None
    if last_event_id is not None:
        try:
            start_after = int(last_event_id)
        except ValueError:
            pass

    return StreamingResponse(
        _event_generator(task_id, request, control_plane, start_after),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering for SSE
        },
    )


@router.websocket("/events")
async def ws_global_events(
    websocket: WebSocket,
) -> None:
    """Global WebSocket firehose for all orchestrator events."""
    await websocket.accept()
    
    global_ws_queues: list[asyncio.Queue] = websocket.app.state.global_ws_queues
    queue: asyncio.Queue = asyncio.Queue()
    global_ws_queues.append(queue)
    
    try:
        await websocket.send_json({"event": "connected", "type": "global_firehose"})
        while True:
            # We must also listen for client disconnects
            receive_task = asyncio.create_task(websocket.receive_text())
            queue_task = asyncio.create_task(queue.get())
            
            done, pending = await asyncio.wait(
                [receive_task, queue_task], return_when=asyncio.FIRST_COMPLETED
            )
            
            if receive_task in done:
                # Client sent something or disconnected
                try:
                    await receive_task
                except WebSocketDisconnect:
                    break
            
            if queue_task in done:
                event = await queue_task
                await websocket.send_json({
                    "event": "task_event",
                    "task_id": event.task_id,
                    "data": event.model_dump()
                })
                
            for task in pending:
                task.cancel()
    except WebSocketDisconnect:
        pass
    finally:
        if queue in global_ws_queues:
            global_ws_queues.remove(queue)


@router.websocket("/tasks/{task_id}/events/ws")
async def ws_task_events(
    websocket: WebSocket,
    task_id: str,
    control_plane: AtlasControlPlane = Depends(get_control_plane),
) -> None:
    """WebSocket stream for a specific task."""
    await websocket.accept()
    
    ws_queues: dict[str, list[asyncio.Queue]] = websocket.app.state.ws_queues
    queue: asyncio.Queue = asyncio.Queue()
    
    if task_id not in ws_queues:
        ws_queues[task_id] = []
    ws_queues[task_id].append(queue)
    
    try:
        await websocket.send_json({"event": "connected", "task_id": task_id})
        
        # Initial snapshot
        initial_events = await control_plane.task_events(task_id, after_sequence=None)
        for event in initial_events:
            await websocket.send_json({
                "event": "task_event",
                "id": event.sequence,
                "data": event.model_dump()
            })
            
        task = await control_plane.get_task(task_id)
        if task.state in _TERMINAL_STATES:
            await websocket.send_json({"event": "stream_closed", "state": task.state})
            return

        while True:
            receive_task = asyncio.create_task(websocket.receive_text())
            queue_task = asyncio.create_task(queue.get())
            
            done, pending = await asyncio.wait(
                [receive_task, queue_task], return_when=asyncio.FIRST_COMPLETED
            )
            
            if receive_task in done:
                try:
                    await receive_task
                except WebSocketDisconnect:
                    break
                    
            if queue_task in done:
                event = await queue_task
                await websocket.send_json({
                    "event": "task_event",
                    "id": event.sequence,
                    "data": event.model_dump()
                })
                
                # Check terminal state
                t = await control_plane.get_task(task_id)
                if t.state in _TERMINAL_STATES:
                    await websocket.send_json({"event": "stream_closed", "state": t.state})
                    break
                    
            for task_pending in pending:
                task_pending.cancel()

    except WebSocketDisconnect:
        pass
    finally:
        task_qs = ws_queues.get(task_id, [])
        if queue in task_qs:
            task_qs.remove(queue)
        if not task_qs:
            ws_queues.pop(task_id, None)
