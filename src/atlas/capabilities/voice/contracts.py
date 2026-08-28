"""Voice contracts — the audio<->text boundary.

Frozen Pydantic models + Protocols shared by every voice provider and the
``VoiceService``. This module is pure data + interfaces: it imports nothing from
``orchestration`` (or any higher layer), so the engine stays low in the
``capabilities`` layer and ``lint-imports`` stays green.

TTS/STT deliberately do NOT reuse the chat ``Provider`` protocol
(``intelligence.providers.base``): that contract is ``complete``/``stream`` over
text, which does not fit an audio stream.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field


class AudioFormat(StrEnum):
    """Container/codec for synthesized audio. Values match provider wire names."""

    MP3 = "mp3"
    WAV = "wav"
    PCM = "pcm"
    OPUS = "opus"


class TTSRequest(BaseModel):
    """A single text-to-speech synthesis request."""

    model_config = {"frozen": True}
    text: str
    voice: str = ""  # provider-specific voice id; "" = provider default
    language: str = "en"  # BCP-47-ish; steers voice/model selection
    audio_format: AudioFormat = AudioFormat.MP3
    sample_rate: int = 24000


class AudioChunk(BaseModel):
    """One chunk of a streamed synthesis. ``done`` marks the final chunk."""

    model_config = {"frozen": True}
    data: bytes = b""
    mime: str = "audio/mpeg"
    seq: int = 0
    done: bool = False
    error: str | None = None  # set on a graceful in-band failure (never raised)


class STTResult(BaseModel):
    """A transcription hypothesis. ``is_final`` distinguishes partial vs settled."""

    model_config = {"frozen": True}
    text: str
    is_final: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class VoiceHealth(BaseModel):
    """Lightweight provider health probe result."""

    model_config = {"frozen": True}
    ok: bool
    detail: str = ""


@runtime_checkable
class TTSProvider(Protocol):
    """Text -> streamed audio."""

    name: str

    def synthesize(self, req: TTSRequest) -> AsyncIterator[AudioChunk]: ...

    async def health(self) -> VoiceHealth: ...

    async def close(self) -> None: ...


@runtime_checkable
class STTProvider(Protocol):
    """Audio -> text (streaming and one-shot)."""

    name: str

    def transcribe_stream(self, pcm: AsyncIterator[bytes]) -> AsyncIterator[STTResult]: ...

    async def transcribe(self, pcm: bytes) -> STTResult: ...

    async def health(self) -> VoiceHealth: ...

    async def close(self) -> None: ...


class VoiceError(Exception):
    """Raised by a provider when synthesis/transcription cannot proceed.

    The ``VoiceService`` catches this to fall back to the other provider; it
    never escapes the service boundary as a raw provider error.
    """
