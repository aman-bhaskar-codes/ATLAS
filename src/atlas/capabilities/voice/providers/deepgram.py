"""Deepgram voice provider — TTS (``/v1/speak``) + STT (Flux, ``/v1/listen`` WS).

Low-latency, English-first. TTS streams audio chunks over HTTP; STT streams
partial/final transcripts over a WebSocket. Auth header is
``Authorization: Token <DEEPGRAM_API_KEY>`` for both.

Testability: ``client`` (httpx) and ``ws_connect`` (an async context-manager
factory matching ``websockets.connect``) are injectable so unit tests run with
fakes and never touch the network.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx

from atlas.capabilities.voice.contracts import (
    AudioChunk,
    STTResult,
    TTSRequest,
    VoiceError,
    VoiceHealth,
)
from atlas.infra.logging import get_logger

_log = get_logger("atlas.capabilities.voice.deepgram")

_TTS_URL = "https://api.deepgram.com/v1/speak"
_STT_URL = "wss://api.deepgram.com/v1/listen"


class DeepgramProvider:
    """Deepgram TTS + streaming STT."""

    name = "deepgram"

    def __init__(
        self,
        api_key: str,
        *,
        tts_model: str = "aura-2-thalia-en",
        stt_model: str = "flux-general-en",
        timeout_s: float = 60.0,
        client: httpx.AsyncClient | None = None,
        ws_connect: Callable[..., Any] | None = None,
    ) -> None:
        self._api_key = api_key
        self._tts_model = tts_model
        self._stt_model = stt_model
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=timeout_s, write=10.0, pool=10.0)
        )
        self._ws_connect = ws_connect

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Token {self._api_key}"}

    # ── TTS ────────────────────────────────────────────────────────────
    async def synthesize(self, req: TTSRequest) -> AsyncIterator[AudioChunk]:
        if not self._api_key:
            raise VoiceError("Deepgram has no API key (set DEEPGRAM_API_KEY)")

        params = {"model": self._tts_model, "encoding": self._encoding(req)}
        seq = 0
        try:
            async with self._client.stream(
                "POST",
                _TTS_URL,
                headers=self._headers,
                params=params,
                json={"text": req.text},
            ) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", "replace")
                    raise VoiceError(f"Deepgram TTS HTTP {resp.status_code}: {body[:200]}")
                mime = resp.headers.get("content-type", "audio/mpeg")
                async for data in resp.aiter_bytes():
                    if not data:
                        continue
                    yield AudioChunk(data=data, mime=mime, seq=seq)
                    seq += 1
        except VoiceError:
            raise
        except Exception as exc:  # transport failure -> provider error
            raise VoiceError(f"Deepgram TTS failed: {type(exc).__name__}({exc})") from exc
        yield AudioChunk(mime="audio/mpeg", seq=seq, done=True)

    @staticmethod
    def _encoding(req: TTSRequest) -> str:
        return {"mp3": "mp3", "wav": "linear16", "pcm": "linear16", "opus": "opus"}.get(req.audio_format.value, "mp3")

    # ── STT ────────────────────────────────────────────────────────────
    async def transcribe_stream(self, pcm: AsyncIterator[bytes]) -> AsyncIterator[STTResult]:
        if not self._api_key:
            raise VoiceError("Deepgram has no API key (set DEEPGRAM_API_KEY)")
        connect = self._ws_connect
        if connect is None:
            try:
                import websockets

                connect = websockets.connect
            except Exception as exc:  # pragma: no cover - dep always declared
                raise VoiceError(f"websockets unavailable for Deepgram STT: {exc}") from exc

        url = f"{_STT_URL}?model={self._stt_model}&smart_format=true"
        try:
            async with connect(url, additional_headers=self._headers) as ws:

                async def _pump() -> None:
                    async for frame in pcm:
                        await ws.send(frame)
                    # signal end-of-audio to Deepgram
                    await ws.send(json.dumps({"type": "CloseStream"}))

                pump = asyncio.ensure_future(_pump())
                try:
                    async for raw in ws:
                        result = self._parse_stt(raw)
                        if result is not None:
                            yield result
                finally:
                    pump.cancel()
        except VoiceError:
            raise
        except Exception as exc:
            raise VoiceError(f"Deepgram STT stream failed: {type(exc).__name__}({exc})") from exc

    async def transcribe(self, pcm: bytes) -> STTResult:
        if not self._api_key:
            raise VoiceError("Deepgram has no API key (set DEEPGRAM_API_KEY)")
        try:
            resp = await self._client.post(
                "https://api.deepgram.com/v1/listen",
                headers={**self._headers, "Content-Type": "audio/wav"},
                params={"model": self._stt_model, "smart_format": "true"},
                content=pcm,
            )
            resp.raise_for_status()
            data = resp.json()
            alt = data["results"]["channels"][0]["alternatives"][0]
            return STTResult(
                text=alt.get("transcript", ""),
                is_final=True,
                confidence=float(alt.get("confidence", 0.0)),
            )
        except Exception as exc:
            raise VoiceError(f"Deepgram transcription failed: {type(exc).__name__}({exc})") from exc

    @staticmethod
    def _parse_stt(raw: str | bytes) -> STTResult | None:
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            return None
        if payload.get("type") not in (None, "Results"):
            return None
        channel = payload.get("channel") or {}
        alts = channel.get("alternatives") or []
        if not alts:
            return None
        alt = alts[0]
        text = alt.get("transcript", "")
        if not text:
            return None
        return STTResult(
            text=text,
            is_final=bool(payload.get("is_final", False)),
            confidence=float(alt.get("confidence", 0.0)),
        )

    async def health(self) -> VoiceHealth:
        return VoiceHealth(ok=bool(self._api_key), detail="key present" if self._api_key else "no api key")

    async def close(self) -> None:
        await self._client.aclose()
