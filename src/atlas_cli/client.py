"""HTTP and WebSocket client for communicating with the ATLAS Gateway."""

import json
from collections.abc import AsyncGenerator
from typing import Any, cast

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

    async def emit_event(self, topic: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Emit a manual event via the REST API."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/api/v1/events/emit", json={"topic": topic, "payload": payload}, timeout=5.0
            )
            # Don't raise for status here to let the CLI handle API errors gracefully
            return cast(dict[str, Any], resp.json())

    async def replay_event(self, event_id: str) -> dict[str, Any]:
        """Replay an historical event via the REST API."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{self.base_url}/api/v1/events/{event_id}/replay", json={}, timeout=5.0)
            return cast(dict[str, Any], resp.json())

    async def trigger_consolidation(self) -> dict[str, Any]:
        """Trigger memory consolidation (episodic -> semantic/proposals)."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{self.base_url}/api/v1/learning/consolidate", timeout=30.0)
            resp.raise_for_status()
            return cast(dict[str, Any], resp.json())

    async def trigger_promotion(self, limit: int = 20) -> dict[str, Any]:
        """Trigger experience-to-skill promotion."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{self.base_url}/api/v1/learning/promote", params={"limit": limit}, timeout=30.0)
            resp.raise_for_status()
            return cast(dict[str, Any], resp.json())

    # ── voice ────────────────────────────────────────────────────────────
    #
    # PRIVACY: these three calls are the only ones that ship audio off the
    # machine — the gateway forwards it to the configured speech API.

    async def speak(self, text: str, language: str | None = None) -> AsyncGenerator[bytes]:
        """Stream synthesized audio bytes for ``text``."""
        payload: dict[str, Any] = {"text": text}
        if language:
            payload["language"] = language
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0)) as client:
            async with client.stream("POST", f"{self.base_url}/api/v1/voice/speak", json=payload) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", "replace")
                    raise RuntimeError(f"voice/speak HTTP {resp.status_code}: {body[:200]}")
                async for chunk in resp.aiter_bytes():
                    if chunk:
                        yield chunk

    async def transcribe(self, audio: bytes) -> dict[str, Any]:
        """Transcribe raw audio bytes to text."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/api/v1/voice/transcribe",
                content=audio,
                headers={"Content-Type": "application/octet-stream"},
                timeout=120.0,
            )
            resp.raise_for_status()
            return cast(dict[str, Any], resp.json())

    def voice_ws_url(self) -> str:
        """The bidirectional voice endpoint (audio in -> task -> audio out)."""
        return f"{self.ws_url}/api/v1/ws/voice"
