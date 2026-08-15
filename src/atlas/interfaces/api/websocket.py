"""WebSocket connection manager for real-time event streaming.

WHY ConnectionManager: centralized client tracking, broadcast fan-out, and graceful
disconnect handling. WHY ping/pong: WebSocket connections can silently die; periodic
keepalive ensures we detect and cleanup dead connections before they accumulate.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from atlas.infra.bus import Event, MessageBus
from atlas.infra.logging import get_logger

_log = get_logger("atlas.api.ws")


class ConnectionManager:
    """Manages active WebSocket connections and broadcasts events."""

    def __init__(self) -> None:
        self._active: dict[str, WebSocket] = {}
        self._client_filters: dict[str, dict[str, Any]] = {}  # client_id -> filters
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, client_id: str | None = None) -> str:
        """Accept a WebSocket connection and assign it a unique client_id."""
        await websocket.accept()
        cid = client_id or str(uuid.uuid4())
        
        async with self._lock:
            self._active[cid] = websocket
            self._client_filters[cid] = {}
        
        _log.info("ws.connected", event_type="websocket", client_id=cid, 
                  total_clients=len(self._active))
        return cid

    async def disconnect(self, client_id: str) -> None:
        """Remove a client from active connections."""
        async with self._lock:
            if client_id in self._active:
                self._active.pop(client_id)
                self._client_filters.pop(client_id, None)
        
        _log.info("ws.disconnected", event_type="websocket", client_id=client_id,
                  total_clients=len(self._active))

    def set_filter(self, client_id: str, **filters: Any) -> None:
        """Set filters for a specific client (e.g., task_id for scoped streams)."""
        if client_id in self._client_filters:
            self._client_filters[client_id].update(filters)

    async def send_personal(self, client_id: str, message: dict[str, Any]) -> bool:
        """Send a message to a specific client. Returns True if sent successfully."""
        websocket = self._active.get(client_id)
        if not websocket:
            return False
        
        try:
            await websocket.send_json(message)
            return True
        except Exception as exc:
            _log.warning("ws.send_failed", event_type="websocket", 
                        client_id=client_id, error=str(exc))
            await self.disconnect(client_id)
            return False

    async def broadcast(self, event: Event) -> None:
        """Broadcast an event to all connected clients that match filters."""
        if not self._active:
            return
        
        event_dict = event.model_dump()
        dead_clients: list[str] = []
        
        # Snapshot of active clients to avoid holding lock during sends
        async with self._lock:
            clients = list(self._active.items())
        
        for client_id, websocket in clients:
            # Check if client's filters match this event
            filters = self._client_filters.get(client_id, {})
            if not self._matches_filters(event_dict, filters):
                continue
            
            try:
                await websocket.send_json(event_dict)
            except Exception as exc:
                _log.warning("ws.broadcast_failed", event_type="websocket",
                           client_id=client_id, error=str(exc))
                dead_clients.append(client_id)
        
        # Cleanup dead connections
        for cid in dead_clients:
            await self.disconnect(cid)

    @staticmethod
    def _matches_filters(event: dict[str, Any], filters: dict[str, Any]) -> bool:
        """Check if an event matches the client's filters."""
        if not filters:
            return True  # No filters = receive everything
        
        for key, value in filters.items():
            if event.get(key) != value:
                return False
        return True

    async def ping_all(self) -> None:
        """Send ping to all clients to check connection health."""
        dead_clients: list[str] = []
        
        async with self._lock:
            clients = list(self._active.items())
        
        for client_id, websocket in clients:
            try:
                await websocket.send_json({"type": "ping", "timestamp": asyncio.get_event_loop().time()})
            except Exception:
                dead_clients.append(client_id)
        
        for cid in dead_clients:
            await self.disconnect(cid)

    def get_stats(self) -> dict[str, Any]:
        """Get connection statistics."""
        return {
            "total_connections": len(self._active),
            "clients": list(self._active.keys()),
        }


class EventBroadcaster:
    """Subscribes to MessageBus and broadcasts events to WebSocket clients."""
    
    def __init__(self, bus: MessageBus, manager: ConnectionManager) -> None:
        self._bus = bus
        self._manager = manager
        self._tasks: list[asyncio.Task[None]] = []
    
    def start(self) -> None:
        """Start subscribing to all event types and broadcasting."""
        # Subscribe to all event types
        topics = ["orchestrator", "safety", "planning", "memory", "tool"]
        
        for topic in topics:
            self._bus.subscribe(topic, self._broadcast_handler)
        
        # Start keepalive ping task
        self._tasks.append(asyncio.create_task(self._keepalive_loop()))
        
        _log.info("ws.broadcaster_started", event_type="websocket",
                 subscribed_topics=topics)
    
    async def _broadcast_handler(self, event: Event) -> None:
        """Handler called by MessageBus for each event."""
        await self._manager.broadcast(event)
    
    async def _keepalive_loop(self) -> None:
        """Periodic ping to keep connections alive and detect dead ones."""
        while True:
            await asyncio.sleep(30)  # Ping every 30 seconds
            await self._manager.ping_all()
    
    async def stop(self) -> None:
        """Stop the broadcaster and cleanup tasks."""
        for task in self._tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()


class MemoryBroadcaster:
    """Subscribes ONLY to the 'memory' bus topic and broadcasts MemoryEvents
    to the dedicated memory ConnectionManager.

    WHY separate from EventBroadcaster: the global broadcaster fans out ALL
    topics. Memory clients only want MemoryEvent objects. A dedicated manager
    keeps the fan-out scoped and adds the '_topic' field automatically so the
    frontend can distinguish memory events from other event types.
    """

    def __init__(self, bus: MessageBus, manager: ConnectionManager) -> None:
        self._bus = bus
        self._manager = manager
        self._tasks: list[asyncio.Task[None]] = []

    def start(self) -> None:
        """Subscribe to the 'memory' topic and start keepalive."""
        self._bus.subscribe("memory", self._broadcast_handler)
        self._tasks.append(asyncio.create_task(self._keepalive_loop()))
        _log.info("ws.memory_broadcaster_started", event_type="websocket")

    async def _broadcast_handler(self, event: Event) -> None:
        """Annotate the raw event dict with _topic, then fan out."""
        payload = event.model_dump()
        payload["_topic"] = "memory"   # so clients can identify message origin
        dead_clients: list[str] = []

        async with self._manager._lock:
            clients = list(self._manager._active.items())

        for client_id, websocket in clients:
            try:
                await websocket.send_json(payload)
            except Exception as exc:
                _log.warning("ws.memory_broadcast_failed", event_type="websocket",
                             client_id=client_id, error=str(exc))
                dead_clients.append(client_id)

        for cid in dead_clients:
            await self._manager.disconnect(cid)

    async def _keepalive_loop(self) -> None:
        while True:
            await asyncio.sleep(30)
            await self._manager.ping_all()

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()
