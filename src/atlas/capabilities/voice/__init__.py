"""Voice package — pure audio<->text engine (STT/TTS providers + VoiceService).

Layer note: this package lives in ``capabilities`` and MUST NOT import
``orchestration`` or any higher layer. The speech->task loop (turning a
transcript into an ``InboundEvent`` and calling ``orchestrator.run()``) lives in
``interfaces``.
"""

from __future__ import annotations

from atlas.capabilities.voice.contracts import (
    AudioChunk,
    AudioFormat,
    STTProvider,
    STTResult,
    TTSProvider,
    TTSRequest,
    VoiceError,
    VoiceHealth,
)
from atlas.capabilities.voice.service import VoiceService

__all__ = [
    "AudioChunk",
    "AudioFormat",
    "STTProvider",
    "STTResult",
    "TTSProvider",
    "TTSRequest",
    "VoiceError",
    "VoiceHealth",
    "VoiceService",
]
