"""Model catalog guard — the curated fleet is exactly the 5 OpenRouter free models.

These assertions are the typo net for `config/models.yaml`: the `:free` provider
slugs are corrected by hand against openrouter.ai/models, and the tier lists in
`config/settings.yaml` reference model ids by string. A mistyped id silently
produces an empty candidate set at selection time, so it is caught here instead.
"""

from __future__ import annotations

from pathlib import Path

from atlas.infra.config import load_app_config
from atlas.infra.types import CostClass
from atlas.intelligence.registry.model_registry import ModelRegistry

_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"

EXPECTED_IDS = {
    "glm-5.2-free",
    "minimax-m3-free",
    "nemotron-3-ultra-free",
    "north-mini-code-free",
    "laguna-s-2.1-free",
}


def _registry() -> ModelRegistry:
    return ModelRegistry.from_yaml(_CONFIG_DIR / "models.yaml")


def test_catalog_is_exactly_the_five_curated_models() -> None:
    specs = _registry().all(include_disabled=True)
    assert {s.id for s in specs} == EXPECTED_IDS
    assert len(specs) == 5


def test_every_model_is_enabled_free_quota_openrouter() -> None:
    for spec in _registry().all(include_disabled=True):
        assert spec.enabled, f"{spec.id} is disabled"
        assert spec.cost_class is CostClass.FREE_QUOTA, f"{spec.id} is {spec.cost_class}"
        assert spec.provider == "openrouter", f"{spec.id} uses provider {spec.provider}"
        assert spec.usd_per_1m_input == 0.0 and spec.usd_per_1m_output == 0.0
        assert spec.provider_model.endswith(":free"), f"{spec.id} slug {spec.provider_model} is not :free"


def test_no_ollama_or_other_providers_remain() -> None:
    providers = {s.provider for s in _registry().all(include_disabled=True)}
    assert providers == {"openrouter"}


def test_tier_lists_reference_real_model_ids() -> None:
    config = load_app_config(_CONFIG_DIR)
    registry = _registry()
    tiers = {
        "fast_models": config.models.fast_models,
        "deep_models": config.models.deep_models,
        "fallback_models": config.models.fallback_models,
    }
    for tier, ids in tiers.items():
        assert ids, f"{tier} is empty"
        for model_id in ids:
            assert registry.get(model_id) is not None, f"{tier} references unknown model {model_id}"


def test_default_and_heavy_models_exist() -> None:
    registry = _registry()
    assert registry.get("glm-5.2-free") is not None  # Settings.default_model
    assert registry.get("nemotron-3-ultra-free") is not None  # Settings.heavy_model


def test_startup_free_model_sync_is_gated_off() -> None:
    # Auto-registering *every* free OpenRouter model would defeat the curated
    # fleet, so the sync must stay off by default.
    assert load_app_config(_CONFIG_DIR).models.sync_openrouter_free is False


def test_multimodal_and_coding_roles_are_covered() -> None:
    registry = _registry()
    vision = [s.id for s in registry.all() if s.supports_vision]
    assert "minimax-m3-free" in vision
    coders = [s.id for s in registry.all() if "coding" in {c.value for c in s.capabilities}]
    assert {"north-mini-code-free", "laguna-s-2.1-free"} <= set(coders)
