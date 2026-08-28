"""The single-key promise, at the config layer.

WHY: "one OpenRouter key runs chat, embeddings and speech" is enforced by
``Settings.effective_embed_api_key()`` and by the embed defaults pointing at
OpenRouter. If a default drifts back to a second vendor, embeddings silently
start failing with "no API key" on a machine that has a perfectly good key.
"""

from __future__ import annotations

from atlas.infra.config import Settings


def _settings(*, openrouter: str = "", embed: str = "") -> Settings:
    # These fields are alias-only, and naming both keeps the test independent of
    # the developer's real .env.
    return Settings(OPENROUTER_API_KEY=openrouter, ATLAS_EMBED_API_KEY=embed)  # type: ignore[call-arg]


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
    def test_embeddings_default_to_openrouter(self) -> None:
        settings = _settings()
        assert settings.embed_base_url == "https://openrouter.ai/api/v1"
        assert settings.embed_provider == "openrouter"

    def test_default_embed_model_is_1024_dim_compatible(self) -> None:
        """qwen3-embedding-0.6b is 1024-dim, as bge-m3/jina-v3 were.

        Changing this to a model of another width silently breaks every existing
        Chroma collection, so the id is pinned here as a tripwire.
        """
        assert _settings().embed_model == "qwen/qwen3-embedding-0.6b"
