"""Fish Audio voice provider — TTS only (``/v1/tts``).

Fish Audio S2.1 Pro: expressive, multilingual (strong Hindi). Used as the
Hindi/multilingual/expressive TTS path and as Deepgram's TTS fallback. Auth
header is ``Authorization: Bearer <FISH_AUDIO_API_KEY>``.

Testability: ``client`` (httpx) is injectable so unit tests run with fakes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from atlas.capabilities.voice.contracts import AudioChunk, TTSRequest, VoiceError, VoiceHealth
from atlas.infra.logging import get_logger

_log = get_logger("atlas.capabilities.voice.fish_audio")

_TTS_URL = "https://api.fish.audio/v1/tts"


class FishAudioProvider:
    """Fish Audio S2.1 Pro text-to-speech (streaming)."""

    name = "fish_audio"

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "s2.1-pro",
        timeout_s: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=timeout_s, write=10.0, pool=10.0)
        )

    @property
    def _headers(self) -> dict[str, str]:
        # `model` header selects the backbone; body carries the utterance.
        return {"Authorization": f"Bearer {self._api_key}", "model": self._model}

    async def synthesize(self, req: TTSRequest) -> AsyncIterator[AudioChunk]:
        if not self._api_key:
            raise VoiceError("Fish Audio has no API key (set FISH_AUDIO_API_KEY)")

        body: dict[str, object] = {
            "text": req.text,
            "format": req.audio_format.value,
            "latency": "balanced",
        }
        if req.voice:
            body["reference_id"] = req.voice

        seq = 0
        try:
            async with self._client.stream("POST", _TTS_URL, headers=self._headers, json=body) as resp:
                if resp.status_code >= 400:
                    payload = (await resp.aread()).decode("utf-8", "replace")
                    raise VoiceError(f"Fish Audio TTS HTTP {resp.status_code}: {payload[:200]}")
                mime = resp.headers.get("content-type", f"audio/{req.audio_format.value}")
                async for data in resp.aiter_bytes():
                    if not data:
                        continue
                    yield AudioChunk(data=data, mime=mime, seq=seq)
                    seq += 1
        except VoiceError:
            raise
        except Exception as exc:
            raise VoiceError(f"Fish Audio TTS failed: {type(exc).__name__}({exc})") from exc
        yield AudioChunk(mime=f"audio/{req.audio_format.value}", seq=seq, done=True)

    async def health(self) -> VoiceHealth:
        return VoiceHealth(ok=bool(self._api_key), detail="key present" if self._api_key else "no api key")

    async def close(self) -> None:
        await self._client.aclose()
