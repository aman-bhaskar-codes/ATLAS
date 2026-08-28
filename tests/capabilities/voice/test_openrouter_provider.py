"""The OpenRouter voice provider is the one-key speech path — pin its wire shape.

WHY: TTS and STT here must speak OpenRouter's documented audio API exactly
(``input`` not ``text``; base64 ``input_audio`` not multipart; raw audio bytes
back, JSON on error). Nothing else in the tree checks that, and a live 400 is the
only other way to find out. These tests use a mocked transport — no network, no
microphone, no key.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from atlas.capabilities.voice.contracts import AudioFormat, TTSRequest, VoiceError
from atlas.capabilities.voice.providers.openrouter import OpenRouterVoiceProvider, wrap_pcm_as_wav


def _provider(handler: Any, **kwargs: Any) -> OpenRouterVoiceProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenRouterVoiceProvider("or-key", client=client, **kwargs)


async def _collect(it: AsyncIterator[Any]) -> list[Any]:
    return [item async for item in it]


class TestWavWrapper:
    def test_header_is_riff_wave_with_the_declared_sizes(self) -> None:
        pcm = b"\x01\x02" * 100
        wav = wrap_pcm_as_wav(pcm, sample_rate=16000)
        assert wav[:4] == b"RIFF"
        assert wav[8:12] == b"WAVE"
        assert len(wav) == len(pcm) + 44
        assert int.from_bytes(wav[4:8], "little") == 36 + len(pcm)
        assert int.from_bytes(wav[24:28], "little") == 16000  # sample rate
        assert int.from_bytes(wav[40:44], "little") == len(pcm)  # data chunk size


class TestSynthesize:
    async def test_posts_the_documented_body_and_streams_audio(self) -> None:
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("authorization")
            import json

            seen["body"] = json.loads(request.content)
            return httpx.Response(200, content=b"ID3audio", headers={"content-type": "audio/mpeg"})

        provider = _provider(handler, tts_model="openai/gpt-4o-mini-tts", voice="alloy")
        chunks = await _collect(provider.synthesize(TTSRequest(text="hello", language="en")))

        assert seen["url"] == "https://openrouter.ai/api/v1/audio/speech"
        assert seen["auth"] == "Bearer or-key"
        # `input`, not `text` — the field name is the whole contract here.
        assert seen["body"] == {
            "model": "openai/gpt-4o-mini-tts",
            "input": "hello",
            "response_format": "mp3",
            "voice": "alloy",
        }
        assert b"".join(c.data for c in chunks) == b"ID3audio"
        assert chunks[-1].done is True
        assert all(c.error is None for c in chunks)

    async def test_pcm_and_wav_both_request_raw_pcm(self) -> None:
        formats: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            import json

            formats.append(json.loads(request.content)["response_format"])
            return httpx.Response(200, content=b"\x00\x01")

        provider = _provider(handler)
        for fmt in (AudioFormat.PCM, AudioFormat.WAV, AudioFormat.OPUS):
            await _collect(provider.synthesize(TTSRequest(text="x", audio_format=fmt)))

        # /audio/speech accepts mp3|pcm only; wav degrades to pcm, opus to mp3.
        assert formats == ["pcm", "pcm", "mp3"]

    async def test_request_voice_overrides_the_configured_default(self) -> None:
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            import json

            seen["body"] = json.loads(request.content)
            return httpx.Response(200, content=b"a")

        provider = _provider(handler, voice="alloy")
        await _collect(provider.synthesize(TTSRequest(text="x", voice="nova")))
        assert seen["body"]["voice"] == "nova"

    async def test_voice_is_omitted_when_neither_is_set(self) -> None:
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            import json

            seen["body"] = json.loads(request.content)
            return httpx.Response(200, content=b"a")

        provider = _provider(handler, voice="")
        await _collect(provider.synthesize(TTSRequest(text="x")))
        assert "voice" not in seen["body"], "an empty voice must not be sent"

    async def test_http_error_surfaces_as_voice_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(402, json={"error": {"message": "insufficient credits"}})

        provider = _provider(handler)
        with pytest.raises(VoiceError) as excinfo:
            await _collect(provider.synthesize(TTSRequest(text="x")))
        assert "402" in str(excinfo.value)
        assert "credits" in str(excinfo.value)

    async def test_transport_failure_surfaces_as_voice_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("dns")

        provider = _provider(handler)
        with pytest.raises(VoiceError):
            await _collect(provider.synthesize(TTSRequest(text="x")))

    async def test_missing_key_is_a_voice_error_not_a_request(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("must not call the API without a key")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = OpenRouterVoiceProvider("", client=client)
        with pytest.raises(VoiceError, match="OPENROUTER_API_KEY"):
            await _collect(provider.synthesize(TTSRequest(text="x")))


class TestTranscribe:
    async def test_sends_base64_wav_and_reads_the_text(self) -> None:
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            import json

            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"text": "turn on the lights", "usage": {"seconds": 1.2}})

        provider = _provider(handler, stt_model="openai/whisper-large-v3")
        result = await provider.transcribe(b"\x00\x01raw-pcm")

        import base64

        assert seen["url"] == "https://openrouter.ai/api/v1/audio/transcriptions"
        assert seen["body"]["model"] == "openai/whisper-large-v3"
        assert seen["body"]["input_audio"]["format"] == "wav"
        decoded = base64.b64decode(seen["body"]["input_audio"]["data"])
        assert decoded[:4] == b"RIFF", "headerless PCM must be wrapped before upload"
        assert decoded.endswith(b"\x00\x01raw-pcm")
        assert result.text == "turn on the lights"
        assert result.is_final is True

    async def test_existing_wav_container_is_not_double_wrapped(self) -> None:
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            import json

            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"text": "ok"})

        wav = wrap_pcm_as_wav(b"\x05" * 8)
        await _provider(handler).transcribe(wav)

        import base64

        assert base64.b64decode(seen["body"]["input_audio"]["data"]) == wav

    async def test_empty_audio_short_circuits(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("must not upload empty audio")

        result = await _provider(handler).transcribe(b"")
        assert result.text == ""
        assert result.is_final is True

    async def test_http_error_surfaces_as_voice_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"error": {"message": "Model not found"}})

        with pytest.raises(VoiceError, match="404"):
            await _provider(handler).transcribe(b"pcm")

    async def test_missing_text_field_yields_zero_confidence(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"usage": {"seconds": 0.0}})

        result = await _provider(handler).transcribe(b"pcm")
        assert result.text == ""
        assert result.confidence == 0.0


class TestTranscribeStream:
    async def test_buffers_the_stream_into_one_final_result(self) -> None:
        uploaded: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            import base64
            import json

            data = json.loads(request.content)["input_audio"]["data"]
            uploaded.append(len(base64.b64decode(data)))
            return httpx.Response(200, json={"text": "hello there"})

        async def frames() -> AsyncIterator[bytes]:
            yield b"aaaa"
            yield b""  # empty frames are dropped, not uploaded as silence
            yield b"bbbb"

        results = await _collect(_provider(handler).transcribe_stream(frames()))

        assert [r.text for r in results] == ["hello there"]
        assert results[0].is_final is True
        assert uploaded == [8 + 44], "both frames concatenated, then WAV-wrapped once"

    async def test_silent_stream_yields_nothing(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("must not upload an empty buffer")

        async def frames() -> AsyncIterator[bytes]:
            return
            yield b""  # pragma: no cover - makes this an async generator

        assert await _collect(_provider(handler).transcribe_stream(frames())) == []


class TestHealth:
    async def test_health_reflects_key_presence(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("health must not call the network")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        assert (await OpenRouterVoiceProvider("k", client=client).health()).ok is True
        assert (await OpenRouterVoiceProvider("", client=client).health()).ok is False
