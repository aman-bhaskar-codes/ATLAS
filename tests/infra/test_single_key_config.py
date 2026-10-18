"""Local embedding defaults and optional OpenRouter key reuse."""

from __future__ import annotations

from atlas.infra.config import Settings


def _settings(*, openrouter: str = "", embed: str = "") -> Settings:
    # These fields are alias-only, and naming both keeps the test independent of
    # the developer's real .env.
    return Settings(_env_file=None, OPENROUTER_API_KEY=openrouter, ATLAS_EMBED_API_KEY=embed)  # type: ignore[call-arg]


class TestEffectiveEmbedApiKey:
    def test_falls_back_to_the_openrouter_key(self) -> None:
        assert _settings(openrouter="or-key").effective_embed_api_key() == "or-key"

    def test_explicit_embed_key_wins(self) -> None:
        """Pointing ATLAS_EMBED_BASE_URL elsewhere needs its own key to win."""
        assert _settings(openrouter="or-key", embed="jina-key").effective_embed_api_key() == "jina-key"

    def test_no_keys_at_all_is_empty_not_an_error(self) -> None:
        # CloudEmbedder turns this into an EmbeddingError at call time, and the
        # semantic cache degrades to a miss — startup must not fail here.
        assert _settings().effective_embed_api_key() == ""


class TestEmbedDefaults:
    def test_embeddings_default_to_local_ollama(self) -> None:
        settings = _settings()
        assert settings.embed_base_url == "https://openrouter.ai/api/v1"
        assert settings.embed_provider == "ollama"

    def test_default_embed_model_uses_installed_local_bge(self) -> None:
        assert _settings().embed_model == "bge-m3:latest"
