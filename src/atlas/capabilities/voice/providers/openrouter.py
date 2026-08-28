"""OpenRouter voice provider — TTS (``/audio/speech``) + STT (``/audio/transcriptions``).

WHY this exists: OpenRouter serves speech on the *same* base URL and the *same*
``OPENROUTER_API_KEY`` as chat completions, so voice needs no extra vendor
account. That is the whole point — one key for the fleet, the embeddings, and the
microphone. Deepgram/Fish Audio remain available as direct providers for anyone
who wants their own keys, but they are no longer required.

Model ids are injected (never hardcoded in logic) because OpenRouter slugs churn:
correcting one is a config edit. Documented defaults live in ``VoiceCfg``.

Shape of the two calls (per OpenRouter's audio API docs):
- ``POST {base}/audio/speech`` -> ``{model, input, voice, response_format}``;
  the body of a 2xx response is *raw audio bytes*, a non-2xx is a JSON error.
- ``POST {base}/audio/transcriptions`` -> ``{model, input_audio: {data, format}}``
  where ``data`` is base64 of the raw file bytes (not a data URI); the response is
  ``{"text": ..., "usage": {...}}``.

Testability: ``client`` (httpx) is injectable so unit tests never touch the network.
"""

from __future__ import annotations

import base64
import struct
from collections.abc import AsyncIterator

import httpx

from atlas.capabilities.voice.contracts import (
    AudioChunk,
    STTResult,
    TTSRequest,
    VoiceError,
    VoiceHealth,
)
from atlas.infra.logging import get_logger

_log = get_logger("atlas.capabilities.voice.openrouter")


def wrap_pcm_as_wav(pcm: bytes, *, sample_rate: int = 16000, channels: int = 1, bits: int = 16) -> bytes:
    """Prepend a 44-byte RIFF/WAVE header to raw little-endian PCM.

    WHY: the microphone path produces headerless linear16 frames, but
    ``/audio/transcriptions`` accepts container formats only (wav, mp3, flac,
    ...). Wrapping here keeps the ``capabilities`` layer free of an audio
    dependency (no soundfile/numpy) for what is 44 bytes of arithmetic.
    """
    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8
    header = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt "
    header += struct.pack("<IHHIIHH", 16, 1, channels, sample_rate, byte_rate, block_align, bits)
    header += b"data" + struct.pack("<I", len(pcm))
    return header + pcm


class OpenRouterVoiceProvider:
    """OpenRouter text-to-speech + speech-to-text over one API key.

    A single instance is both a ``TTSProvider`` and an ``STTProvider``. ``name``
    is a constructor argument so the composition root can register two instances
    that differ only in ``tts_model`` (for example an English low-latency voice
    and an expressive multilingual one) and let ``VoiceService`` route and fall
    back between them by name.
    """

    def __init__(
        self,
        api_key: str,
        *,
        name: str = "openrouter",
        base_url: str = "https://openrouter.ai/api/v1",
        tts_model: str = "openai/gpt-4o-mini-tts",
        stt_model: str = "openai/whisper-large-v3",
        voice: str = "alloy",
        sample_rate: int = 16000,
        timeout_s: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.name = name
        self._api_key = api_key
        self._base = base_url.rstrip("/")
        self._tts_model = tts_model
        self._stt_model = stt_model
        self._voice = voice
        self._sample_rate = sample_rate
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=timeout_s, write=10.0, pool=10.0)
        )

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    @property
    def tts_model(self) -> str:
        """The synthesis model this instance is pinned to (two instances differ)."""
        return self._tts_model

    @property
    def stt_model(self) -> str:
        return self._stt_model

    def _require_key(self, what: str) -> None:
        if not self._api_key:
            raise VoiceError(f"OpenRouter {what} has no API key (set OPENROUTER_API_KEY)")

    # ── TTS ────────────────────────────────────────────────────────────
    async def synthesize(self, req: TTSRequest) -> AsyncIterator[AudioChunk]:
        self._require_key("TTS")

        # /audio/speech supports mp3 and pcm only; wav/opus degrade to raw pcm.
        response_format = "pcm" if req.audio_format.value in ("pcm", "wav") else "mp3"
        body: dict[str, object] = {
            "model": self._tts_model,
            "input": req.text,
            "response_format": response_format,
        }
        voice = req.voice or self._voice
        if voice:
            body["voice"] = voice

        default_mime = "audio/pcm" if response_format == "pcm" else "audio/mpeg"
        seq = 0
        try:
            async with self._client.stream(
                "POST", f"{self._base}/audio/speech", headers=self._headers, json=body
            ) as resp:
                if resp.status_code >= 400:
                    payload = (await resp.aread()).decode("utf-8", "replace")
                    raise VoiceError(f"OpenRouter TTS HTTP {resp.status_code}: {payload[:200]}")
                mime = resp.headers.get("content-type", default_mime)
                async for data in resp.aiter_bytes():
                    if not data:
                        continue
                    yield AudioChunk(data=data, mime=mime, seq=seq)
                    seq += 1
        except VoiceError:
            raise
        except Exception as exc:  # transport failure -> provider error
            raise VoiceError(f"OpenRouter TTS failed: {type(exc).__name__}({exc})") from exc
        yield AudioChunk(mime=default_mime, seq=seq, done=True)

    # ── STT ────────────────────────────────────────────────────────────
    async def transcribe(self, pcm: bytes) -> STTResult:
        self._require_key("STT")
        if not pcm:
            return STTResult(text="", is_final=True)

        # Already a container (mic wrote a WAV, or a file was uploaded)? Pass through.
        audio = pcm if pcm[:4] == b"RIFF" else wrap_pcm_as_wav(pcm, sample_rate=self._sample_rate)
        body = {
            "model": self._stt_model,
            "input_audio": {"data": base64.b64encode(audio).decode("ascii"), "format": "wav"},
        }
        try:
            resp = await self._client.post(f"{self._base}/audio/transcriptions", headers=self._headers, json=body)
            if resp.status_code >= 400:
                raise VoiceError(f"OpenRouter STT HTTP {resp.status_code}: {resp.text[:200]}")
            data = resp.json()
        except VoiceError:
            raise
        except Exception as exc:
            raise VoiceError(f"OpenRouter transcription failed: {type(exc).__name__}({exc})") from exc

        text = str(data.get("text", "") or "")
        # The endpoint reports no per-utterance confidence; a settled transcript
        # is reported as fully confident rather than inventing a score.
        return STTResult(text=text, is_final=True, confidence=1.0 if text else 0.0)

    async def transcribe_stream(self, pcm: AsyncIterator[bytes]) -> AsyncIterator[STTResult]:
        """Buffer the stream, then transcribe once.

        WHY no partials: OpenRouter's transcription endpoint is request/response,
        not a live socket, so there is nothing to emit until the audio ends. The
        contract still holds (an iterator of ``STTResult``) — it just yields a
        single final result. Use ``DeepgramProvider`` when true interim
        hypotheses matter.
        """
        buffer = bytearray()
        async for frame in pcm:
            if frame:
                buffer.extend(frame)
        if not buffer:
            return
        _log.debug("voice.stt_buffered", event_type="capability", provider=self.name, bytes=len(buffer))
        yield await self.transcribe(bytes(buffer))

    async def health(self) -> VoiceHealth:
        return VoiceHealth(ok=bool(self._api_key), detail="key present" if self._api_key else "no api key")

    async def close(self) -> None:
        await self._client.aclose()
