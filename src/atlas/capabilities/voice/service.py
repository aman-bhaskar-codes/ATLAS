"""VoiceService — language-aware TTS with fallback + streaming STT.

The one object the interfaces layer talks to. It owns a list of TTS providers and
an optional STT provider, and applies the routing policy by *name*: the
configured primary handles English/low-latency, the configured fallback handles
Hindi/multilingual/expressive, and each covers for the other when it errors (same
idea as ``FallbackEmbedder``).

By default both names are OpenRouter instances built from the single
``OPENROUTER_API_KEY`` — differing only in TTS model — so the fallback costs no
extra account. Deepgram / Fish Audio slot into the same list when their optional
keys are set.

Boundary contract: ``speak`` never raises a provider error past its boundary —
on total failure it emits a final ``AudioChunk`` with ``error`` set. Only
``asyncio.CancelledError`` propagates (cooperative cancellation).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from atlas.capabilities.voice.contracts import (
    AudioChunk,
    STTProvider,
    STTResult,
    TTSProvider,
    TTSRequest,
    VoiceError,
)
from atlas.infra.logging import get_logger

_log = get_logger("atlas.capabilities.voice.service")


class VoiceService:
    def __init__(
        self,
        tts_providers: list[TTSProvider],
        stt: STTProvider | None = None,
        *,
        default_language: str = "en",
        english_provider: str = "deepgram",
        multilingual_provider: str = "fish_audio",
    ) -> None:
        if not tts_providers:
            raise ValueError("VoiceService needs at least one TTS provider")
        self._tts = {p.name: p for p in tts_providers}
        self._order = [p.name for p in tts_providers]
        self._stt = stt
        self._default_language = default_language
        self._english = english_provider
        self._multilingual = multilingual_provider

    def _ordered_for(self, language: str) -> list[TTSProvider]:
        """Preferred provider first, then the rest as fallbacks."""
        preferred = self._english if language.lower().startswith("en") else self._multilingual
        names = [preferred] + [n for n in self._order if n != preferred]
        return [self._tts[n] for n in names if n in self._tts]

    # ── introspection (health endpoints, diagnostics, tests) ───────────
    def provider_names(self) -> list[str]:
        """Registered TTS provider names, in registration order."""
        return list(self._order)

    def provider(self, name: str) -> TTSProvider | None:
        return self._tts.get(name)

    def stt_name(self) -> str | None:
        return None if self._stt is None else self._stt.name

    def ordered_names_for(self, language: str) -> list[str]:
        """The order ``speak`` would try providers in for ``language``."""
        return [p.name for p in self._ordered_for(language)]

    async def speak(self, text: str, language: str | None = None) -> AsyncIterator[AudioChunk]:
        lang = language or self._default_language
        providers = self._ordered_for(lang)
        req = TTSRequest(text=text, language=lang)

        last_error: str = "no TTS provider available"
        for provider in providers:
            produced = False
            try:
                async for chunk in provider.synthesize(req):
                    produced = produced or bool(chunk.data)
                    yield chunk
                return  # provider completed the stream
            except asyncio.CancelledError:
                raise
            except VoiceError as exc:
                last_error = f"{provider.name}: {exc}"
                _log.warning("voice.tts_fallback", event_type="capability", provider=provider.name, error=str(exc))
                if produced:
                    # Mid-stream failure after real audio: cannot cleanly restart.
                    yield AudioChunk(done=True, error=last_error)
                    return
                # No audio yet — fall through to the next provider.
            except Exception as exc:  # unexpected provider bug: contain it
                last_error = f"{provider.name}: {type(exc).__name__}({exc})"
                _log.error("voice.tts_error", event_type="capability", provider=provider.name, error=str(exc))
                if produced:
                    yield AudioChunk(done=True, error=last_error)
                    return

        yield AudioChunk(done=True, error=last_error)

    async def listen(self, pcm_stream: AsyncIterator[bytes]) -> AsyncIterator[STTResult]:
        if self._stt is None:
            raise VoiceError("VoiceService has no STT provider configured")
        async for result in self._stt.transcribe_stream(pcm_stream):
            yield result

    async def transcribe(self, pcm: bytes) -> STTResult:
        if self._stt is None:
            raise VoiceError("VoiceService has no STT provider configured")
        return await self._stt.transcribe(pcm)

    async def close(self) -> None:
        for provider in self._tts.values():
            try:
                await provider.close()
            except Exception:
                pass
        if self._stt is not None and self._stt.name not in self._tts:
            try:
                await self._stt.close()
            except Exception:
                pass
