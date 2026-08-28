"""Voice API routes — HTTP speak/transcribe, the WS loop, and the disabled path.

`app.state.atlas` is a SimpleNamespace here: the routes only touch
`voice_service`, `ids`, and `orchestrator`, so a full `Atlas` build (and its
network/model dependencies) is unnecessary.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from atlas.capabilities.voice.contracts import AudioChunk, STTResult
from atlas.interfaces.api.routes_voice import router as voice_router


class FakeVoiceService:
    def __init__(
        self,
        *,
        chunks: list[bytes] | None = None,
        transcript: str = "what is the weather",
        confidence: float = 0.91,
    ) -> None:
        self.chunks = chunks if chunks is not None else [b"aud", b"io"]
        self.transcript = transcript
        self.confidence = confidence
        self.spoken: list[tuple[str, str | None]] = []
        self.transcribed: list[bytes] = []

    async def speak(self, text: str, language: str | None = None) -> AsyncIterator[AudioChunk]:
        self.spoken.append((text, language))
        for seq, data in enumerate(self.chunks):
            yield AudioChunk(data=data, seq=seq)
        yield AudioChunk(done=True, seq=len(self.chunks))

    async def transcribe(self, pcm: bytes) -> STTResult:
        self.transcribed.append(pcm)
        return STTResult(text=self.transcript, is_final=True, confidence=self.confidence)


class FakeOrchestrator:
    def __init__(self, answer: str = "It is sunny.", ok: bool = True, error: str | None = None) -> None:
        self.answer = answer
        self.ok = ok
        self.error = error
        self.events: list[object] = []

    async def run(self, event: object) -> SimpleNamespace:
        self.events.append(event)
        return SimpleNamespace(ok=self.ok, answer=self.answer, error=self.error)


def _client(
    voice: FakeVoiceService | None,
    orchestrator: FakeOrchestrator | None = None,
) -> tuple[TestClient, SimpleNamespace]:
    app = FastAPI()
    app.include_router(voice_router, prefix="/api/v1")
    state = SimpleNamespace(
        voice_service=voice,
        orchestrator=orchestrator or FakeOrchestrator(),
        ids=SimpleNamespace(correlation_id=lambda: "corr-voice-1"),
    )
    app.state.atlas = state
    return TestClient(app), state


class TestSpeak:
    def test_streams_synthesized_audio(self) -> None:
        voice = FakeVoiceService(chunks=[b"one", b"two"])
        client, _ = _client(voice)
        resp = client.post("/api/v1/voice/speak", json={"text": "hello", "language": "en"})

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("audio/mpeg")
        assert resp.content == b"onetwo"  # done-marker chunk carries no bytes
        assert voice.spoken == [("hello", "en")]

    def test_language_optional(self) -> None:
        voice = FakeVoiceService()
        client, _ = _client(voice)
        assert client.post("/api/v1/voice/speak", json={"text": "hi"}).status_code == 200
        assert voice.spoken == [("hi", None)]

    def test_missing_text_is_422(self) -> None:
        client, _ = _client(FakeVoiceService())
        assert client.post("/api/v1/voice/speak", json={}).status_code == 422


class TestTranscribe:
    def test_returns_text_and_confidence(self) -> None:
        voice = FakeVoiceService(transcript="summarize my inbox", confidence=0.66)
        client, _ = _client(voice)
        resp = client.post("/api/v1/voice/transcribe", content=b"\x00\x01\x02")

        assert resp.status_code == 200
        body = resp.json()
        assert body["text"] == "summarize my inbox"
        assert body["confidence"] == 0.66
        assert voice.transcribed == [b"\x00\x01\x02"]


class TestDisabledSubsystem:
    def test_speak_returns_503(self) -> None:
        client, _ = _client(None)
        resp = client.post("/api/v1/voice/speak", json={"text": "hi"})
        assert resp.status_code == 503
        assert "disabled" in resp.json()["detail"]

    def test_transcribe_returns_503(self) -> None:
        client, _ = _client(None)
        assert client.post("/api/v1/voice/transcribe", content=b"x").status_code == 503

    def test_websocket_closes_instead_of_crashing(self) -> None:
        client, _ = _client(None)
        with pytest.raises(WebSocketDisconnect) as excinfo:
            with client.websocket_connect("/api/v1/ws/voice"):
                pass
        assert excinfo.value.code == 1011
        assert excinfo.value.reason == "voice disabled"


class TestWebSocketLoop:
    def test_full_turn_transcribes_runs_orchestrator_and_speaks(self) -> None:
        voice = FakeVoiceService(chunks=[b"ans"], transcript="what is the weather")
        orchestrator = FakeOrchestrator(answer="It is sunny.")
        client, _ = _client(voice, orchestrator)

        with client.websocket_connect("/api/v1/ws/voice") as ws:
            ws.send_bytes(b"\x01\x02")
            ws.send_bytes(b"\x03")
            ws.send_text("__end__")
            assert ws.receive_bytes() == b"ans"
            assert ws.receive_text() == "__done__"

        assert voice.transcribed == [b"\x01\x02\x03"]
        assert voice.spoken == [("It is sunny.", None)]

    def test_event_is_tagged_as_voice_source(self) -> None:
        orchestrator = FakeOrchestrator()
        client, _ = _client(FakeVoiceService(chunks=[b"a"]), orchestrator)

        with client.websocket_connect("/api/v1/ws/voice") as ws:
            ws.send_bytes(b"\x01")
            ws.send_text("__end__")
            ws.receive_bytes()
            ws.receive_text()

        event = orchestrator.events[0]
        assert event.source == "voice"  # type: ignore[attr-defined]
        assert event.content == "what is the weather"  # type: ignore[attr-defined]
        assert event.correlation_id == "corr-voice-1"  # type: ignore[attr-defined]

    def test_failed_task_speaks_the_error(self) -> None:
        orchestrator = FakeOrchestrator(answer="", ok=False, error="tool denied by policy")
        voice = FakeVoiceService(chunks=[b"e"])
        client, _ = _client(voice, orchestrator)

        with client.websocket_connect("/api/v1/ws/voice") as ws:
            ws.send_bytes(b"\x01")
            ws.send_text("__end__")
            ws.receive_bytes()
            ws.receive_text()

        assert voice.spoken == [("tool denied by policy", None)]

    def test_empty_transcript_short_circuits_without_orchestrator(self) -> None:
        voice = FakeVoiceService(transcript="   ")
        orchestrator = FakeOrchestrator()
        client, _ = _client(voice, orchestrator)

        with client.websocket_connect("/api/v1/ws/voice") as ws:
            ws.send_bytes(b"\x01")
            ws.send_text("__end__")
            assert ws.receive_text() == "__done__"

        assert orchestrator.events == []
        assert voice.spoken == []

    def test_end_with_no_audio_is_ignored(self) -> None:
        voice = FakeVoiceService(chunks=[b"a"])
        orchestrator = FakeOrchestrator()
        client, _ = _client(voice, orchestrator)

        with client.websocket_connect("/api/v1/ws/voice") as ws:
            ws.send_text("__end__")  # no frames buffered -> loop continues
            ws.send_bytes(b"\x09")
            ws.send_text("__end__")
            assert ws.receive_bytes() == b"a"
            assert ws.receive_text() == "__done__"

        assert voice.transcribed == [b"\x09"]

    def test_unknown_text_frames_are_ignored(self) -> None:
        voice = FakeVoiceService(chunks=[b"a"])
        client, _ = _client(voice)

        with client.websocket_connect("/api/v1/ws/voice") as ws:
            ws.send_text("ping")  # not "__end__": skipped, not fatal
            ws.send_bytes(b"\x07")
            ws.send_text("__end__")
            assert ws.receive_bytes() == b"a"
            assert ws.receive_text() == "__done__"

    def test_multiple_turns_on_one_connection(self) -> None:
        voice = FakeVoiceService(chunks=[b"a"])
        client, _ = _client(voice)

        with client.websocket_connect("/api/v1/ws/voice") as ws:
            for frame in (b"\x01", b"\x02"):
                ws.send_bytes(frame)
                ws.send_text("__end__")
                assert ws.receive_bytes() == b"a"
                assert ws.receive_text() == "__done__"

        assert voice.transcribed == [b"\x01", b"\x02"]

    def test_disconnect_mid_utterance_is_clean(self) -> None:
        voice = FakeVoiceService()
        client, _ = _client(voice)
        with client.websocket_connect("/api/v1/ws/voice") as ws:
            ws.send_bytes(b"\x01")
        assert voice.transcribed == []
