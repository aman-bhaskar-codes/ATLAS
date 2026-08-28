"""Voice bootstrap — build the optional VoiceService from config + keys.

Optional-subsystem template (like ``bootstrap/computer_use.py`` /
``build_browser_platform``): returns ``VoiceComponents`` whose ``service`` is
``None`` when voice is disabled or no usable key is present. Never raises —
voice is a nicety, not a startup dependency.

One key, two voices: ``OPENROUTER_API_KEY`` registers *both* OpenRouter
instances — ``openrouter`` (English/low-latency) and ``openrouter_multilingual``
(Fish Audio S2.1 Pro for Hindi/expressive) — plus OpenRouter STT. Direct vendor
keys are optional and only *add* fallbacks.

PRIVACY: constructing live providers means microphone audio and synthesis text
will be sent to the configured speech API when the service is used.
"""

from __future__ import annotations

from dataclasses import dataclass

from atlas.capabilities.voice.contracts import STTProvider, TTSProvider
from atlas.capabilities.voice.providers.deepgram import DeepgramProvider
from atlas.capabilities.voice.providers.fish_audio import FishAudioProvider
from atlas.capabilities.voice.providers.openrouter import OpenRouterVoiceProvider
from atlas.capabilities.voice.service import VoiceService
from atlas.infra.config import AppConfig, Settings
from atlas.infra.logging import get_logger

_log = get_logger("atlas.bootstrap.voice")

_MULTILINGUAL = "openrouter_multilingual"


@dataclass
class VoiceComponents:
    service: VoiceService | None


def build_voice(settings: Settings, config: AppConfig) -> VoiceComponents:
    cfg = config.voice
    if not cfg.enabled:
        _log.info("voice.disabled", event_type="lifecycle")
        return VoiceComponents(service=None)

    timeout_s = config.models.cloud_timeout_s
    tts: list[TTSProvider] = []
    stt: STTProvider | None = None

    # ── OpenRouter: the default path, one key for both voices + STT ────
    if settings.openrouter_api_key:
        english = OpenRouterVoiceProvider(
            settings.openrouter_api_key,
            name="openrouter",
            tts_model=cfg.openrouter_tts_model,
            stt_model=cfg.openrouter_stt_model,
            voice=cfg.openrouter_tts_voice,
            sample_rate=cfg.sample_rate,
            timeout_s=timeout_s,
        )
        multilingual = OpenRouterVoiceProvider(
            settings.openrouter_api_key,
            name=_MULTILINGUAL,
            tts_model=cfg.openrouter_tts_model_multilingual,
            stt_model=cfg.openrouter_stt_model,
            voice=cfg.openrouter_tts_voice_multilingual,
            sample_rate=cfg.sample_rate,
            timeout_s=timeout_s,
        )
        tts.extend([english, multilingual])
        if cfg.stt_provider.startswith("openrouter"):
            stt = english

    # ── Optional direct-vendor providers (extra fallbacks) ─────────────
    deepgram: DeepgramProvider | None = (
        DeepgramProvider(settings.deepgram_api_key, timeout_s=timeout_s) if settings.deepgram_api_key else None
    )
    if deepgram is not None:
        tts.append(deepgram)
        # Deepgram Flux is the only provider here with true streaming partials,
        # so it wins the STT slot when explicitly configured.
        if cfg.stt_provider == "deepgram" or stt is None:
            stt = deepgram
    if settings.fish_audio_api_key:
        tts.append(FishAudioProvider(settings.fish_audio_api_key, timeout_s=timeout_s))

    if not tts:
        _log.warning(
            "voice.no_keys",
            event_type="lifecycle",
            detail="voice.enabled but no OPENROUTER_API_KEY (or vendor key) — voice unavailable",
        )
        return VoiceComponents(service=None)

    service = VoiceService(
        tts,
        stt,
        default_language=cfg.default_language,
        english_provider=cfg.tts_primary,
        multilingual_provider=cfg.tts_fallback,
    )
    _log.info(
        "voice.ready",
        event_type="lifecycle",
        tts=[p.name for p in tts],
        stt=stt.name if stt is not None else None,
        privacy="audio egress to third-party APIs when used",
    )
    return VoiceComponents(service=service)
