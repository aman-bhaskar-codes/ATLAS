"""The composition root must expose local and policy-eligible cloud routes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from atlas.bootstrap.intelligence import build_intelligence
from atlas.infra.clock import SystemClock
from atlas.infra.config import Settings, load_app_config
from atlas.infra.db import Database
from atlas.infra.ids import UuidGenerator
from atlas.infra.types import CostClass, NetworkPolicy
from atlas.intelligence.capabilities import Capability
from atlas.intelligence.contracts import Constraints
from atlas.intelligence.errors import ConfigurationError
from atlas.memory.embedder import OllamaEmbedder
from atlas.safety.audit import AuditLog

CONFIG = Path(__file__).resolve().parents[2] / "config"


@pytest.fixture
def no_chroma(monkeypatch: pytest.MonkeyPatch) -> None:
    # Vector storage is outside this wiring test and otherwise starts native workers.
    monkeypatch.setattr("atlas.bootstrap.intelligence.ChromaVectorStore", lambda *args, **kwargs: object())


def _settings(tmp_path: Path, **updates: Any) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        data_dir=tmp_path,
        OPENROUTER_API_KEY="",
        ATLAS_EMBED_API_KEY="",
        gemini_api_key="",
        groq_api_key="",
        anthropic_api_key="",
        **updates,
    )


async def _build(settings: Settings) -> Any:
    db = Database(settings.data_dir / "test.db")
    return await build_intelligence(
        settings=settings,
        config=load_app_config(CONFIG),
        config_dir=CONFIG,
        db=db,
        ids=UuidGenerator(),
        clock=SystemClock(),
        audit=AuditLog(db),
    )


async def test_local_bootstrap_registers_ollama_and_local_embeddings(tmp_path: Path, no_chroma: None) -> None:
    components = await _build(_settings(tmp_path, profile="local_free"))
    try:
        assert components.gateway._runtime.provider_names() == ["ollama"]
        assert isinstance(components.embedder, OllamaEmbedder)
        ranked = components.gateway._selector.select(
            frozenset({Capability.REASONING, Capability.TOOL_CALLING}),
            Constraints(network_policy=NetworkPolicy.OFFLINE),
        )
        assert [spec.provider_model for spec in ranked] == ["qwen2.5-coder:7b", "qwen3:4b"]
        assert components.registry.get("local-qwen3").max_concurrency == 1
    finally:
        await components.gateway.close()
        await components.embedder.close()


@pytest.mark.parametrize("billing_tier", ["free", "paid"])
async def test_free_profile_registers_gemini_only_for_declared_free_project(
    tmp_path: Path,
    no_chroma: None,
    billing_tier: str,
) -> None:
    settings = _settings(tmp_path, profile="free_hybrid", gemini_billing_tier=billing_tier)
    settings = settings.model_copy(update={"gemini_api_key": "test-key"})
    components = await _build(settings)
    try:
        assert ("gemini" in components.gateway._runtime.provider_names()) is (billing_tier == "free")
        model = components.registry.get("gemini-flash")
        assert model is not None
        assert model.cost_class is (CostClass.FREE_QUOTA if billing_tier == "free" else CostClass.PAID)
        assert model.usd_per_1m_input == (0 if billing_tier == "free" else 0.75)
    finally:
        await components.gateway.close()
        await components.embedder.close()


async def test_unknown_model_default_fails_with_clear_configuration_error(tmp_path: Path, no_chroma: None) -> None:
    with pytest.raises(ConfigurationError, match=r"unknown.*default_model"):
        await _build(_settings(tmp_path, default_model="not-a-model"))


async def test_local_profile_uses_local_embeddings_even_with_legacy_cloud_setting(
    tmp_path: Path,
    no_chroma: None,
) -> None:
    components = await _build(
        _settings(
            tmp_path,
            profile="local_free",
            embed_provider="openrouter",
            embed_model="qwen/qwen3-embedding-0.6b",
        )
    )
    try:
        assert isinstance(components.embedder, OllamaEmbedder)
        assert components.embedder._model == "bge-m3:latest"
    finally:
        await components.gateway.close()
        await components.embedder.close()


async def test_environment_model_defaults_lead_the_tier_preferences(tmp_path: Path, no_chroma: None) -> None:
    components = await _build(_settings(tmp_path, default_model="gemini-flash", heavy_model="local-qwen3"))
    try:
        assert components.gateway._fast_models[0] == "gemini-flash"
        assert components.gateway._deep_models[0] == "local-qwen3"
        assert "openrouter-free" in components.gateway._fast_models
    finally:
        await components.gateway.close()
        await components.embedder.close()
