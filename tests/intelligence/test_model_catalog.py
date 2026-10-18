"""Guard free routing, explicit paid metadata and valid model preferences.

These assertions are the typo net for `config/models.yaml`: the `:free` provider
slugs are corrected by hand against openrouter.ai/models, and the tier lists in
`config/settings.yaml` reference model ids by string. A mistyped id silently
produces an empty candidate set at selection time, so it is caught here instead.
"""

from __future__ import annotations

from pathlib import Path

from atlas.infra.config import Settings, load_app_config
from atlas.infra.types import CostClass, CostPolicy, NetworkPolicy
from atlas.intelligence.contracts import Constraints
from atlas.intelligence.health.health_monitor import HealthMonitor
from atlas.intelligence.registry.capability_index import CapabilityIndex
from atlas.intelligence.registry.model_registry import ModelRegistry
from atlas.intelligence.selection.selector import ModelSelector

_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"

EXPECTED_IDS = {
    "local-qwen2.5-coder",
    "local-qwen3",
    "local-nomic-embed",
    "gemini-2.5-flash",
    "gemini-flash",
    "glm-5.2",
    "minimax-m3",
    "dots-3-note-free",
    "openrouter-free",
    "nemotron-3-ultra-free",
    "north-mini-code-free",
    "laguna-s-2.1-free",
}


def _registry() -> ModelRegistry:
    return ModelRegistry.from_yaml(_CONFIG_DIR / "models.yaml")


def test_catalog_has_local_gemini_and_preserves_openrouter_routes() -> None:
    specs = _registry().all(include_disabled=True)
    assert {s.id for s in specs} == EXPECTED_IDS
    assert len(specs) == len(EXPECTED_IDS)


def test_free_routes_have_zero_prices_and_paid_routes_have_actual_prices() -> None:
    for spec in _registry().all(include_disabled=True):
        assert spec.enabled, f"{spec.id} is disabled"
        if spec.provider == "ollama":
            assert spec.cost_class is CostClass.LOCAL
            assert spec.usd_per_1m_input == 0.0 and spec.usd_per_1m_output == 0.0
            continue
        if spec.cost_class is CostClass.FREE_QUOTA:
            assert spec.usd_per_1m_input == 0.0 and spec.usd_per_1m_output == 0.0
            assert (
                spec.provider == "gemini"
                or spec.provider_model.endswith(":free")
                or spec.provider_model == "openrouter/free"
            )
        else:
            assert spec.cost_class is CostClass.PAID
            assert spec.usd_per_1m_input > 0 and spec.usd_per_1m_output > 0


def test_local_and_gemini_adapters_have_catalog_routes() -> None:
    providers = {s.provider for s in _registry().all(include_disabled=True)}
    assert providers == {"openrouter", "ollama", "gemini"}


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
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert registry.get(settings.default_model) is not None
    assert registry.get(settings.heavy_model) is not None


def test_startup_free_model_sync_is_gated_off() -> None:
    # Auto-registering *every* free OpenRouter model would defeat the curated
    # fleet, so the sync must stay off by default.
    assert load_app_config(_CONFIG_DIR).models.sync_openrouter_free is False


def test_multimodal_and_coding_roles_are_covered() -> None:
    registry = _registry()
    vision = [s.id for s in registry.all() if s.supports_vision]
    assert "dots-3-note-free" in vision
    coders = [s.id for s in registry.all() if "coding" in {c.value for c in s.capabilities}]
    assert {"north-mini-code-free", "laguna-s-2.1-free"} <= set(coders)


def test_paid_vendor_models_cannot_be_selected_under_free_policy() -> None:
    registry = _registry()
    paid_slugs = {"z-ai/glm-5.2", "minimax/minimax-m3"}
    candidates = ModelSelector(CapabilityIndex(registry), HealthMonitor()).select(
        frozenset(), Constraints(cost_policy=CostPolicy.FREE_ONLY, network_policy=NetworkPolicy.FREE_CLOUD)
    )
    assert not paid_slugs.intersection(spec.provider_model for spec in candidates)
    assert candidates, "the free profile must still have usable candidates"


def test_free_catalog_includes_structured_multimodal_candidate() -> None:
    specs = _registry().all()
    assert any(
        spec.cost_class is CostClass.FREE_QUOTA
        and spec.supports_vision
        and spec.supports_structured_output
        and spec.provider_model == "dots-studio/dots-3-note-preview:free"
        for spec in specs
    )
