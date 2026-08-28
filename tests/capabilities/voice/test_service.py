"""VoiceService — language routing, TTS fallback, and boundary containment.

The service is the only voice object the interfaces layer touches, so its
contract matters: pick the right provider for the language, fall back to the
other one when the first fails before emitting audio, never leak a provider
error past the boundary, and still honour cancellation.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from atlas.capabilities.voice.contracts import AudioChunk, STTResult, TTSRequest, VoiceError
from atlas.capabilities.voice.service import VoiceService


class FakeTTS:
    """Configurable TTS provider: succeed, fail early, or fail mid-stream."""

    def __init__(
        self,
        name: str,
        *,
        chunks: list[bytes] | None = None,
        fail_before_audio: bool = False,
        fail_after_audio: bool = False,
        error: Exception | None = None,
    ) -> None:
        self.name = name
        self.calls: list[TTSRequest] = []
        self.closed = False
        self._chunks = chunks if chunks is not None else [name.encode()]
        self._fail_before = fail_before_audio
        self._fail_after = fail_after_audio
        self._error = error or VoiceError(f"{name} unavailable")

    async def synthesize(self, req: TTSRequest) -> AsyncIterator[AudioChunk]:
        self.calls.append(req)
        if self._fail_before:
            raise self._error
        for seq, data in enumerate(self._chunks):
            yield AudioChunk(data=data, seq=seq)
        if self._fail_after:
            raise self._error
        yield AudioChunk(done=True, seq=len(self._chunks))

    async def health(self) -> None:  # pragma: no cover - unused by these tests
        return None

    async def close(self) -> None:
        self.closed = True


class FakeSTT:
    name = "deepgram"

    def __init__(self, results: list[STTResult] | None = None, error: Exception | None = None) -> None:
        self.results = results or [STTResult(text="hi", is_final=True, confidence=0.9)]
        self.error = error
        self.closed = False
        self.streamed: list[bytes] = []

    async def transcribe_stream(self, pcm: AsyncIterator[bytes]) -> AsyncIterator[STTResult]:
        async for frame in pcm:
            self.streamed.append(frame)
        if self.error is not None:
            raise self.error
        for result in self.results:
            yield result

    async def transcribe(self, pcm: bytes) -> STTResult:
        if self.error is not None:
            raise self.error
        return self.results[-1]

    async def health(self) -> None:  # pragma: no cover
        return None

    async def close(self) -> None:
        self.closed = True


async def _speak(service: VoiceService, text: str, language: str | None = None) -> list[AudioChunk]:
    return [chunk async for chunk in service.speak(text, language)]


def _service(*providers: FakeTTS, stt: FakeSTT | None = None) -> VoiceService:
    return VoiceService(list(providers), stt)


class TestLanguageRouting:
    async def test_english_prefers_deepgram(self) -> None:
        dg, fish = FakeTTS("deepgram"), FakeTTS("fish_audio")
        await _speak(_service(dg, fish), "hello", "en")
        assert dg.calls and not fish.calls

    async def test_hindi_prefers_fish_audio(self) -> None:
        dg, fish = FakeTTS("deepgram"), FakeTTS("fish_audio")
        await _speak(_service(dg, fish), "नमस्ते", "hi")
        assert fish.calls and not dg.calls

    async def test_en_us_variant_still_routes_to_deepgram(self) -> None:
        dg, fish = FakeTTS("deepgram"), FakeTTS("fish_audio")
        await _speak(_service(dg, fish), "hello", "en-US")
        assert dg.calls and not fish.calls

    async def test_default_language_used_when_none_given(self) -> None:
        dg, fish = FakeTTS("deepgram"), FakeTTS("fish_audio")
        service = VoiceService([dg, fish], None, default_language="hi")
        await _speak(service, "नमस्ते")
        assert fish.calls and fish.calls[0].language == "hi"

    async def test_single_provider_serves_every_language(self) -> None:
        only = FakeTTS("deepgram")
        chunks = await _speak(_service(only), "नमस्ते", "hi")
        assert only.calls and chunks[-1].error is None

    def test_empty_provider_list_rejected(self) -> None:
        with pytest.raises(ValueError, match="at least one TTS provider"):
            VoiceService([])


class TestFallback:
    async def test_falls_back_when_primary_fails_before_audio(self) -> None:
        dg = FakeTTS("deepgram", fail_before_audio=True)
        fish = FakeTTS("fish_audio", chunks=[b"backup"])
        chunks = await _speak(_service(dg, fish), "hello", "en")

        assert dg.calls and fish.calls
        assert b"backup" in [c.data for c in chunks]
        assert chunks[-1].done is True and chunks[-1].error is None

    async def test_reverse_fallback_for_multilingual(self) -> None:
        dg = FakeTTS("deepgram", chunks=[b"dg"])
        fish = FakeTTS("fish_audio", fail_before_audio=True)
        chunks = await _speak(_service(dg, fish), "नमस्ते", "hi")
        assert fish.calls and dg.calls
        assert b"dg" in [c.data for c in chunks]

    async def test_mid_stream_failure_ends_with_error_chunk(self) -> None:
        dg = FakeTTS("deepgram", chunks=[b"partial"], fail_after_audio=True)
        fish = FakeTTS("fish_audio", chunks=[b"unused"])
        chunks = await _speak(_service(dg, fish), "hello", "en")

        assert [c.data for c in chunks if c.data] == [b"partial"]
        assert chunks[-1].done is True and chunks[-1].error is not None
        assert not fish.calls, "must not restart a stream that already emitted audio"

    async def test_all_providers_failing_yields_error_chunk_not_exception(self) -> None:
        dg = FakeTTS("deepgram", fail_before_audio=True)
        fish = FakeTTS("fish_audio", fail_before_audio=True)
        chunks = await _speak(_service(dg, fish), "hello", "en")

        assert len(chunks) == 1
        assert chunks[0].done is True
        assert chunks[0].error is not None and "fish_audio" in chunks[0].error

    async def test_unexpected_provider_bug_is_contained(self) -> None:
        dg = FakeTTS("deepgram", fail_before_audio=True, error=RuntimeError("provider bug"))
        fish = FakeTTS("fish_audio", chunks=[b"ok"])
        chunks = await _speak(_service(dg, fish), "hello", "en")
        assert b"ok" in [c.data for c in chunks]

    async def test_cancellation_propagates(self) -> None:
        dg = FakeTTS("deepgram", fail_before_audio=True, error=asyncio.CancelledError())
        fish = FakeTTS("fish_audio")
        with pytest.raises(asyncio.CancelledError):
            await _speak(_service(dg, fish), "hello", "en")
        assert not fish.calls, "cancellation must not trigger a fallback attempt"


class TestListenAndTranscribe:
    async def test_listen_yields_results_from_stream(self) -> None:
        stt = FakeSTT(
            [
                STTResult(text="partial", is_final=False, confidence=0.4),
                STTResult(text="final text", is_final=True, confidence=0.95),
            ]
        )
        service = _service(FakeTTS("deepgram"), stt=stt)

        async def pcm() -> AsyncIterator[bytes]:
            yield b"\x00\x01"
            yield b"\x02\x03"

        results = [r async for r in service.listen(pcm())]
        assert [r.text for r in results] == ["partial", "final text"]
        assert stt.streamed == [b"\x00\x01", b"\x02\x03"]

    async def test_transcribe_one_shot(self) -> None:
        stt = FakeSTT([STTResult(text="spoken words", is_final=True, confidence=0.8)])
        result = await _service(FakeTTS("deepgram"), stt=stt).transcribe(b"audio")
        assert result.text == "spoken words"

    async def test_listen_without_stt_raises(self) -> None:
        service = _service(FakeTTS("deepgram"))

        async def pcm() -> AsyncIterator[bytes]:
            yield b"x"

        with pytest.raises(VoiceError, match="no STT provider"):
            [r async for r in service.listen(pcm())]

    async def test_transcribe_without_stt_raises(self) -> None:
        with pytest.raises(VoiceError, match="no STT provider"):
            await _service(FakeTTS("deepgram")).transcribe(b"x")


class TestTeardown:
    async def test_close_closes_every_provider_once(self) -> None:
        dg, fish, stt = FakeTTS("deepgram"), FakeTTS("fish_audio"), FakeSTT()
        await _service(dg, fish, stt=stt).close()
        assert dg.closed and fish.closed

    async def test_close_closes_standalone_stt(self) -> None:
        stt = FakeSTT()
        stt.name = "other_stt"
        fish = FakeTTS("fish_audio")
        await _service(fish, stt=stt).close()
        assert fish.closed and stt.closed

    async def test_close_survives_provider_errors(self) -> None:
        class Exploding(FakeTTS):
            async def close(self) -> None:
                raise RuntimeError("teardown boom")

        await _service(Exploding("deepgram"), FakeTTS("fish_audio")).close()
