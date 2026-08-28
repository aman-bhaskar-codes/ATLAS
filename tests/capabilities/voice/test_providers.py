"""Deepgram + Fish Audio providers against fakes — no network, no audio devices.

Contract under test: streamed chunks are assembled and terminated by a
``done`` chunk, auth headers are correct, and every failure surfaces as
``VoiceError`` rather than a raw transport exception.
"""

from __future__ import annotations

import json

import pytest

from atlas.capabilities.voice.contracts import AudioChunk, AudioFormat, TTSRequest, VoiceError
from atlas.capabilities.voice.providers.deepgram import DeepgramProvider
from atlas.capabilities.voice.providers.fish_audio import FishAudioProvider
from tests.capabilities.voice.fakes import (
    FakeHttpClient,
    FakeJsonResponse,
    FakeStreamResponse,
    FakeWebSocket,
    pcm_stream,
    results_frame,
    ws_connect_factory,
)


async def _collect(provider: object, req: TTSRequest) -> list[AudioChunk]:
    return [chunk async for chunk in provider.synthesize(req)]  # type: ignore[attr-defined]


class TestDeepgramTTS:
    async def test_streams_chunks_then_done(self) -> None:
        client = FakeHttpClient(stream_response=FakeStreamResponse([b"aa", b"", b"bb"]))
        provider = DeepgramProvider("key", client=client)
        chunks = await _collect(provider, TTSRequest(text="hello"))

        assert [c.data for c in chunks] == [b"aa", b"bb", b""]
        assert chunks[-1].done is True and chunks[-1].error is None
        assert [c.seq for c in chunks[:2]] == [0, 1]

    async def test_sends_token_auth_and_model(self) -> None:
        client = FakeHttpClient(stream_response=FakeStreamResponse([b"x"]))
        provider = DeepgramProvider("secret", tts_model="aura-2-thalia-en", client=client)
        await _collect(provider, TTSRequest(text="hi"))

        method, url, kwargs = client.stream_calls[0]
        assert method == "POST" and url.endswith("/v1/speak")
        assert kwargs["headers"]["Authorization"] == "Token secret"
        assert kwargs["params"]["model"] == "aura-2-thalia-en"
        assert kwargs["json"] == {"text": "hi"}

    async def test_wav_request_maps_to_linear16(self) -> None:
        client = FakeHttpClient(stream_response=FakeStreamResponse([b"x"]))
        provider = DeepgramProvider("k", client=client)
        await _collect(provider, TTSRequest(text="hi", audio_format=AudioFormat.WAV))
        assert client.stream_calls[0][2]["params"]["encoding"] == "linear16"

    async def test_http_error_raises_voice_error(self) -> None:
        client = FakeHttpClient(stream_response=FakeStreamResponse(status_code=402, error_body=b"quota"))
        provider = DeepgramProvider("k", client=client)
        with pytest.raises(VoiceError, match="402"):
            await _collect(provider, TTSRequest(text="hi"))

    async def test_transport_error_raises_voice_error(self) -> None:
        client = FakeHttpClient(stream_error=RuntimeError("connection reset"))
        provider = DeepgramProvider("k", client=client)
        with pytest.raises(VoiceError, match="Deepgram TTS failed"):
            await _collect(provider, TTSRequest(text="hi"))

    async def test_missing_key_raises_before_any_call(self) -> None:
        client = FakeHttpClient(stream_response=FakeStreamResponse([b"x"]))
        provider = DeepgramProvider("", client=client)
        with pytest.raises(VoiceError, match="DEEPGRAM_API_KEY"):
            await _collect(provider, TTSRequest(text="hi"))
        assert client.stream_calls == []


class TestDeepgramSTT:
    async def test_stream_yields_transcripts_and_pumps_audio(self) -> None:
        ws = FakeWebSocket([results_frame("hello", is_final=False), results_frame("hello world")])
        captured: dict[str, object] = {}
        provider = DeepgramProvider("k", client=FakeHttpClient(), ws_connect=ws_connect_factory(ws, captured))

        results = [r async for r in provider.transcribe_stream(pcm_stream(b"\x01\x02", b"\x03\x04"))]

        assert [(r.text, r.is_final) for r in results] == [("hello", False), ("hello world", True)]
        assert b"\x01\x02" in ws.sent and b"\x03\x04" in ws.sent
        assert json.dumps({"type": "CloseStream"}) in ws.sent
        assert "flux-general-en" in str(captured["url"])
        assert captured["kwargs"]["additional_headers"]["Authorization"] == "Token k"  # type: ignore[index]

    async def test_non_results_and_empty_frames_are_ignored(self) -> None:
        frames = [
            "not json at all",
            json.dumps({"type": "Metadata"}),
            json.dumps({"type": "Results", "channel": {"alternatives": []}}),
            results_frame(""),
            results_frame("kept"),
        ]
        ws = FakeWebSocket(frames)
        provider = DeepgramProvider("k", client=FakeHttpClient(), ws_connect=ws_connect_factory(ws, {}))
        results = [r async for r in provider.transcribe_stream(pcm_stream(b"z"))]
        assert [r.text for r in results] == ["kept"]

    async def test_connect_failure_raises_voice_error(self) -> None:
        connect = ws_connect_factory(FakeWebSocket([]), {}, error=RuntimeError("handshake failed"))
        provider = DeepgramProvider("k", client=FakeHttpClient(), ws_connect=connect)
        with pytest.raises(VoiceError, match="Deepgram STT stream failed"):
            [r async for r in provider.transcribe_stream(pcm_stream(b"z"))]

    async def test_one_shot_transcribe(self) -> None:
        payload = {
            "results": {"channels": [{"alternatives": [{"transcript": "one shot", "confidence": 0.77}]}]},
        }
        client = FakeHttpClient(post_response=FakeJsonResponse(payload))
        provider = DeepgramProvider("k", client=client)
        result = await provider.transcribe(b"RIFFfake")

        assert result.text == "one shot" and result.is_final is True
        assert result.confidence == pytest.approx(0.77)
        url, kwargs = client.post_calls[0]
        assert url.endswith("/v1/listen")
        assert kwargs["headers"]["Authorization"] == "Token k"
        assert kwargs["content"] == b"RIFFfake"

    async def test_one_shot_error_raises_voice_error(self) -> None:
        client = FakeHttpClient(post_error=RuntimeError("timeout"))
        provider = DeepgramProvider("k", client=client)
        with pytest.raises(VoiceError, match="Deepgram transcription failed"):
            await provider.transcribe(b"x")

    async def test_health_reflects_key_presence(self) -> None:
        assert (await DeepgramProvider("k", client=FakeHttpClient()).health()).ok is True
        assert (await DeepgramProvider("", client=FakeHttpClient()).health()).ok is False

    async def test_close_closes_client(self) -> None:
        client = FakeHttpClient()
        await DeepgramProvider("k", client=client).close()
        assert client.closed is True


class TestFishAudio:
    async def test_streams_chunks_then_done(self) -> None:
        client = FakeHttpClient(
            stream_response=FakeStreamResponse([b"h1", b"h2"], headers={"content-type": "audio/mpeg"})
        )
        provider = FishAudioProvider("key", client=client)
        chunks = await _collect(provider, TTSRequest(text="नमस्ते", language="hi"))

        assert [c.data for c in chunks] == [b"h1", b"h2", b""]
        assert chunks[-1].done is True

    async def test_sends_bearer_auth_and_model_header(self) -> None:
        client = FakeHttpClient(stream_response=FakeStreamResponse([b"x"]))
        provider = FishAudioProvider("secret", model="s2.1-pro", client=client)
        await _collect(provider, TTSRequest(text="hi", voice="ref-123"))

        method, url, kwargs = client.stream_calls[0]
        assert method == "POST" and url.endswith("/v1/tts")
        assert kwargs["headers"]["Authorization"] == "Bearer secret"
        assert kwargs["headers"]["model"] == "s2.1-pro"
        assert kwargs["json"]["text"] == "hi"
        assert kwargs["json"]["reference_id"] == "ref-123"

    async def test_voice_omitted_when_not_requested(self) -> None:
        client = FakeHttpClient(stream_response=FakeStreamResponse([b"x"]))
        await _collect(FishAudioProvider("k", client=client), TTSRequest(text="hi"))
        assert "reference_id" not in client.stream_calls[0][2]["json"]

    async def test_http_error_raises_voice_error(self) -> None:
        client = FakeHttpClient(stream_response=FakeStreamResponse(status_code=429, error_body=b"slow down"))
        with pytest.raises(VoiceError, match="429"):
            await _collect(FishAudioProvider("k", client=client), TTSRequest(text="hi"))

    async def test_transport_error_raises_voice_error(self) -> None:
        client = FakeHttpClient(stream_error=TimeoutError())
        with pytest.raises(VoiceError, match="Fish Audio TTS failed"):
            await _collect(FishAudioProvider("k", client=client), TTSRequest(text="hi"))

    async def test_missing_key_raises_before_any_call(self) -> None:
        client = FakeHttpClient(stream_response=FakeStreamResponse([b"x"]))
        with pytest.raises(VoiceError, match="FISH_AUDIO_API_KEY"):
            await _collect(FishAudioProvider("", client=client), TTSRequest(text="hi"))
        assert client.stream_calls == []
