"""``build_voice`` must produce a working service from ONE OpenRouter key.

WHY: the single-key promise ("chat, embeddings and speech on
``OPENROUTER_API_KEY``") lives entirely in this bootstrap's wiring — which
providers it registers, and which one lands in the STT slot. A regression here
would only show up as silence at runtime. No network: nothing is called, only
constructed.
"""

from __future__ import annotations

from atlas.bootstrap.voice import build_voice
from atlas.infra.config import AppConfig, Settings, VoiceCfg


def _config(**voice: object) -> AppConfig:
    return AppConfig(voice=VoiceCfg(enabled=True, **voice))  # type: ignore[arg-type]


def _settings(*, openrouter: str = "", deepgram: str = "", fish: str = "") -> Settings:
    """Settings with every voice-relevant key pinned.

    WHY the aliases: these three fields carry ``validation_alias``, so they are
    only settable by their env name — and every key must be named explicitly or
    ``Settings`` would read the developer's real ``.env`` and make these tests
    depend on whoever runs them.
    """
    return Settings(  # type: ignore[call-arg]
        OPENROUTER_API_KEY=openrouter,
        DEEPGRAM_API_KEY=deepgram,
        FISH_AUDIO_API_KEY=fish,
    )


class TestDisabledOrUnkeyed:
    def test_disabled_returns_no_service(self) -> None:
        components = build_voice(_settings(openrouter="k"), AppConfig())
        assert components.service is None

    def test_enabled_without_any_key_returns_no_service(self) -> None:
        """voice.enabled with no keys must degrade, not raise."""
        components = build_voice(_settings(), _config())
        assert components.service is None


class TestOneOpenRouterKey:
    def test_registers_both_voices_and_the_stt_slot(self) -> None:
        service = build_voice(_settings(openrouter="or-key"), _config()).service
        assert service is not None
        assert service.provider_names() == ["openrouter", "openrouter_multilingual"]
        assert service.stt_name() == "openrouter"

    def test_english_routes_to_the_primary_and_hindi_to_the_multilingual_voice(self) -> None:
        service = build_voice(_settings(openrouter="or-key"), _config()).service
        assert service is not None
        # Preferred provider first, the other retained as the fallback.
        assert service.ordered_names_for("en") == ["openrouter", "openrouter_multilingual"]
        assert service.ordered_names_for("hi") == ["openrouter_multilingual", "openrouter"]

    def test_the_two_voices_use_different_tts_models(self) -> None:
        """One key, two models — otherwise the fallback is not a real fallback."""
        cfg = _config(
            openrouter_tts_model="openai/gpt-4o-mini-tts",
            openrouter_tts_model_multilingual="fish-audio/s2.1-pro",
        )
        service = build_voice(_settings(openrouter="or-key"), cfg).service
        assert service is not None
        models = {name: service.provider(name).tts_model for name in service.provider_names()}  # type: ignore[union-attr]
        assert models == {
            "openrouter": "openai/gpt-4o-mini-tts",
            "openrouter_multilingual": "fish-audio/s2.1-pro",
        }


class TestOptionalVendorKeys:
    def test_deepgram_key_adds_a_fallback_and_wins_the_stt_slot(self) -> None:
        settings = _settings(openrouter="or-key", deepgram="dg")
        service = build_voice(settings, _config(stt_provider="deepgram")).service
        assert service is not None
        assert service.provider_names() == ["openrouter", "openrouter_multilingual", "deepgram"]
        assert service.stt_name() == "deepgram", "Flux is the only provider with true partials"

    def test_openrouter_keeps_the_stt_slot_when_configured(self) -> None:
        settings = _settings(openrouter="or-key", deepgram="dg")
        service = build_voice(settings, _config(stt_provider="openrouter")).service
        assert service is not None
        assert service.stt_name() == "openrouter"

    def test_fish_audio_key_adds_a_direct_fallback(self) -> None:
        settings = _settings(openrouter="or-key", fish="fa")
        service = build_voice(settings, _config()).service
        assert service is not None
        assert "fish_audio" in service.provider_names()

    def test_vendor_keys_alone_still_build_a_service(self) -> None:
        """No OpenRouter key: the direct vendors remain a valid configuration."""
        service = build_voice(_settings(deepgram="dg"), _config(tts_primary="deepgram")).service
        assert service is not None
        assert service.provider_names() == ["deepgram"]
        assert service.stt_name() == "deepgram"
