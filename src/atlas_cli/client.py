"""HTTP and WebSocket client for communicating with the ATLAS Gateway."""

import json
from collections.abc import AsyncGenerator
from typing import Any

import httpx
import websockets

class AtlasClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8730") -> None:
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
            return resp.json()
            
    async def get_task(self, task_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{self.base_url}/api/v1/tasks/{task_id}")
            resp.raise_for_status()
            return resp.json()

    async def decide_approval(self, approval_id: str, decision: str) -> None:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/api/v1/approvals/{approval_id}/decide",
                json={"decision": decision},
                timeout=5.0,
            )
            resp.raise_for_status()

    async def stream_task_events(self, task_id: str) -> AsyncGenerator[dict[str, Any]]:
        """Stream task events via WebSocket."""
        uri = f"{self.ws_url}/api/v1/tasks/{task_id}/events/ws"
        async with websockets.connect(uri) as ws:
            while True:
                try:
                    data = await ws.recv()
                    yield json.parse(data) if isinstance(data, str) else json.loads(data)
                except websockets.ConnectionClosed:
                    break
                    
    async def stream_global_events(self) -> AsyncGenerator[dict[str, Any]]:
        """Stream all global events via WebSocket."""
        uri = f"{self.ws_url}/api/v1/events"
        async with websockets.connect(uri) as ws:
            while True:
                try:
                    data = await ws.recv()
                    yield json.loads(data)
                except websockets.ConnectionClosed:
                    break
