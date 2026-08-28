"""The ``atlas voice`` CLI client must agree with the gateway's voice routes.

WHY: the canonical CLI (``atlas_cli``) talks to the gateway over HTTP/WS, so its
URLs are a contract with ``routes_voice.py`` that nothing else checks — a stale
``/api/v1`` prefix or a renamed path would only surface as a 404 in live use.
These tests pin the paths against the real router and cover the streaming and
error paths of the two audio calls with a mocked transport (no network, no mic).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from atlas.interfaces.api.routes_voice import router as voice_router
from atlas_cli.client import AtlasClient

_PREFIX = "/api/v1"


def _route_paths() -> set[str]:
    return {_PREFIX + route.path for route in voice_router.routes}  # type: ignore[attr-defined]


class _FakeStream:
    """Stands in for the async context manager returned by ``client.stream``."""

    def __init__(self, chunks: list[bytes], status_code: int = 200, body: bytes = b"") -> None:
        self.status_code = status_code
        self._chunks = chunks
        self._body = body

    async def __aenter__(self) -> _FakeStream:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk

    async def aread(self) -> bytes:
        return self._body


class TestPathsMatchTheRouter:
    def test_speak_and_transcribe_paths_exist(self) -> None:
        paths = _route_paths()
        assert f"{_PREFIX}/voice/speak" in paths
        assert f"{_PREFIX}/voice/transcribe" in paths

    def test_ws_url_matches_the_mounted_websocket_route(self) -> None:
        client = AtlasClient("http://127.0.0.1:8730")
        url = client.voice_ws_url()
        assert url.startswith("ws://127.0.0.1:8730")
        assert url.removeprefix("ws://127.0.0.1:8730") in _route_paths()

    def test_https_base_url_upgrades_the_websocket_scheme(self) -> None:
        assert AtlasClient("https://atlas.example").voice_ws_url().startswith("wss://atlas.example")


class TestSpeak:
    async def test_streams_chunks_and_sends_the_language_hint(self) -> None:
        captured: dict[str, Any] = {}

        def _stream(_self: Any, method: str, url: str, **kwargs: Any) -> _FakeStream:
            captured["method"] = method
            captured["url"] = url
            captured["json"] = kwargs.get("json")
            return _FakeStream([b"aud", b"io"])

        client = AtlasClient()
        with patch("httpx.AsyncClient.stream", new=_stream):
            chunks = [chunk async for chunk in client.speak("नमस्ते", "hi")]

        assert chunks == [b"aud", b"io"]
        assert captured["method"] == "POST"
        assert captured["url"].endswith(f"{_PREFIX}/voice/speak")
        assert captured["json"] == {"text": "नमस्ते", "language": "hi"}

    async def test_language_is_omitted_when_not_given(self) -> None:
        captured: dict[str, Any] = {}

        def _stream(_self: Any, method: str, url: str, **kwargs: Any) -> _FakeStream:
            captured["json"] = kwargs.get("json")
            return _FakeStream([b"x"])

        client = AtlasClient()
        with patch("httpx.AsyncClient.stream", new=_stream):
            _ = [chunk async for chunk in client.speak("hello")]

        assert captured["json"] == {"text": "hello"}, "an empty language must not be sent"

    async def test_disabled_voice_surfaces_the_503_body(self) -> None:
        def _stream(_self: Any, method: str, url: str, **kwargs: Any) -> _FakeStream:
            return _FakeStream([], status_code=503, body=b'{"detail":"voice subsystem is disabled"}')

        client = AtlasClient()
        with patch("httpx.AsyncClient.stream", new=_stream):
            with pytest.raises(RuntimeError) as excinfo:
                _ = [chunk async for chunk in client.speak("hello")]

        assert "503" in str(excinfo.value)
        assert "disabled" in str(excinfo.value)

    async def test_empty_chunks_are_dropped(self) -> None:
        def _stream(_self: Any, method: str, url: str, **kwargs: Any) -> _FakeStream:
            return _FakeStream([b"a", b"", b"b"])

        client = AtlasClient()
        with patch("httpx.AsyncClient.stream", new=_stream):
            chunks = [chunk async for chunk in client.speak("hello")]

        assert chunks == [b"a", b"b"]


class TestTranscribe:
    async def test_posts_raw_audio_and_returns_the_payload(self) -> None:
        resp = MagicMock()
        resp.json.return_value = {"text": "turn on the lights", "confidence": 0.94}
        resp.raise_for_status.return_value = None
        post = AsyncMock(return_value=resp)

        client = AtlasClient()
        with patch("httpx.AsyncClient.post", new=post):
            result = await client.transcribe(b"\x00\x01pcm")

        assert result == {"text": "turn on the lights", "confidence": 0.94}
        _, kwargs = post.call_args
        assert post.call_args[0][0].endswith(f"{_PREFIX}/voice/transcribe")
        assert kwargs["content"] == b"\x00\x01pcm"
        assert kwargs["headers"]["Content-Type"] == "application/octet-stream"
