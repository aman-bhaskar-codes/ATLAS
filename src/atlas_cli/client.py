"""HTTP and WebSocket client for communicating with the ATLAS Gateway."""

import json
from collections.abc import AsyncGenerator
from typing import Any, cast

import httpx
import websockets


class AtlasClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8000") -> None:
        self.base_url = base_url.rstrip("/")
        self.ws_url = self.base_url.replace("http://", "ws://").replace("https://", "wss://")

    async def create_task(self, request: str, source: str = "api") -> dict[str, Any]:
        """Start a new task and return the task_id."""
        import uuid

        idempotency_key = str(uuid.uuid4())
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/api/v1/tasks",
                json={"request": request, "source": source, "idempotency_key": idempotency_key},
                timeout=10.0,
            )
            resp.raise_for_status()
            return cast(dict[str, Any], resp.json())

    async def get_task(self, task_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{self.base_url}/api/v1/tasks/{task_id}")
            resp.raise_for_status()
            return cast(dict[str, Any], resp.json())

    async def decide_approval(self, approval_id: str, decision: str) -> None:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/api/v1/approvals/{approval_id}/decide",
                json={"decision": decision},
                timeout=5.0,
            )
            resp.raise_for_status()

    async def stream_task_events(self, task_id: str) -> AsyncGenerator[dict[str, Any]]:
        """Stream task events via WebSocket (Phase 1 endpoint)."""
        uri = f"{self.ws_url}/ws/tasks/{task_id}/stream"
        async with websockets.connect(uri) as ws:
            while True:
                try:
                    data = await ws.recv()
                    event = json.loads(data) if isinstance(data, str) else cast(dict[str, Any], data)

                    # Handle ping/pong
                    if isinstance(event, dict) and event.get("type") == "ping":
                        await ws.send("pong")
                        continue

                    # Skip replay_complete markers
                    if isinstance(event, dict) and event.get("type") == "replay_complete":
                        continue

                    yield cast(dict[str, Any], event)
                except websockets.ConnectionClosed:
                    break

    async def stream_global_events(self) -> AsyncGenerator[dict[str, Any]]:
        """Stream all global events via WebSocket (Phase 1 endpoint)."""
        uri = f"{self.ws_url}/ws/events"
        async with websockets.connect(uri) as ws:
            while True:
                try:
                    data = await ws.recv()
                    event = json.loads(data) if isinstance(data, str) else cast(dict[str, Any], data)

                    # Handle ping/pong
                    if isinstance(event, dict) and event.get("type") == "ping":
                        await ws.send("pong")
                        continue

                    yield cast(dict[str, Any], event)
                except websockets.ConnectionClosed:
                    break

    async def search_events(
        self,
        task_id: str | None = None,
        topic: str | None = None,
        from_ts: str | None = None,
        to_ts: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Search historical events with filters."""
        params: dict[str, Any] = {}
        if task_id:
            params["task_id"] = task_id
        if topic:
            params["topic"] = topic
        if from_ts:
            params["from_ts"] = from_ts
        if to_ts:
            params["to_ts"] = to_ts
        params["limit"] = limit
        params["offset"] = offset

        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{self.base_url}/api/v1/events/search", params=params, timeout=10.0)
            resp.raise_for_status()
            return cast(dict[str, Any], resp.json())
