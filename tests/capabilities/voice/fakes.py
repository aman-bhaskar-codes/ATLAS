"""Voice provider fakes — httpx streaming + websocket stand-ins, zero network.

Both providers accept an injected client (and Deepgram an injected ``ws_connect``)
precisely so these tests never open a socket.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterable
from typing import Any


class FakeStreamResponse:
    """Mimics the object yielded by ``httpx.AsyncClient.stream(...)``."""

    def __init__(
        self,
        chunks: Iterable[bytes] = (),
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        error_body: bytes = b"",
    ) -> None:
        self.status_code = status_code
        self.headers = headers if headers is not None else {"content-type": "audio/mpeg"}
        self._chunks = list(chunks)
        self._error_body = error_body

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk

    async def aread(self) -> bytes:
        return self._error_body


class _StreamCtx:
    def __init__(self, response: FakeStreamResponse) -> None:
        self._response = response

    async def __aenter__(self) -> FakeStreamResponse:
        return self._response

    async def __aexit__(self, *_: object) -> bool:
        return False


class FakeJsonResponse:
    def __init__(self, payload: dict[str, Any], *, status_code: int = 200) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeHttpClient:
    """Records calls; returns a canned stream/post response or raises."""

    def __init__(
        self,
        *,
        stream_response: FakeStreamResponse | None = None,
        stream_error: Exception | None = None,
        post_response: FakeJsonResponse | None = None,
        post_error: Exception | None = None,
    ) -> None:
        self.stream_calls: list[tuple[str, str, dict[str, Any]]] = []
        self.post_calls: list[tuple[str, dict[str, Any]]] = []
        self.closed = False
        self._stream_response = stream_response
        self._stream_error = stream_error
        self._post_response = post_response
        self._post_error = post_error

    def stream(self, method: str, url: str, **kwargs: Any) -> _StreamCtx:
        self.stream_calls.append((method, url, kwargs))
        if self._stream_error is not None:
            raise self._stream_error
        return _StreamCtx(self._stream_response or FakeStreamResponse())

    async def post(self, url: str, **kwargs: Any) -> FakeJsonResponse:
        self.post_calls.append((url, kwargs))
        if self._post_error is not None:
            raise self._post_error
        return self._post_response or FakeJsonResponse({})

    async def aclose(self) -> None:
        self.closed = True


class FakeWebSocket:
    def __init__(self, frames: Iterable[str | bytes]) -> None:
        self._frames = list(frames)
        self.sent: list[Any] = []

    async def send(self, data: Any) -> None:
        self.sent.append(data)

    async def __aiter__(self) -> AsyncIterator[str | bytes]:
        # Yield the pump a window to drain the pcm stream before the read loop
        # finishes and cancels it — keeps `sent` assertions deterministic.
        await asyncio.sleep(0.02)
        for frame in self._frames:
            yield frame


class _WsCtx:
    def __init__(self, ws: FakeWebSocket) -> None:
        self._ws = ws

    async def __aenter__(self) -> FakeWebSocket:
        return self._ws

    async def __aexit__(self, *_: object) -> bool:
        return False


def ws_connect_factory(
    ws: FakeWebSocket,
    captured: dict[str, Any],
    *,
    error: Exception | None = None,
) -> Any:
    def connect(url: str, **kwargs: Any) -> _WsCtx:
        captured["url"] = url
        captured["kwargs"] = kwargs
        if error is not None:
            raise error
        return _WsCtx(ws)

    return connect


def results_frame(transcript: str, *, is_final: bool = True, confidence: float = 0.9) -> str:
    """A Deepgram ``Results`` frame carrying one alternative."""
    return json.dumps(
        {
            "type": "Results",
            "is_final": is_final,
            "channel": {"alternatives": [{"transcript": transcript, "confidence": confidence}]},
        }
    )


async def pcm_stream(*frames: bytes) -> AsyncIterator[bytes]:
    for frame in frames:
        yield frame
