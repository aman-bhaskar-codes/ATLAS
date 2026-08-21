from atlas.infra.cognition import ModelTier
from atlas.infra.types import CostClass, CostPolicy, NetworkPolicy
from atlas.intelligence.capabilities import Capability
from atlas.intelligence.contracts import ModelSpec
from atlas.intelligence.health.health_monitor import HealthMonitor
from atlas.intelligence.registry.capability_index import CapabilityIndex
from atlas.intelligence.registry.model_registry import ModelRegistry
from atlas.intelligence.selection.selector import Constraints, ModelSelector  # type: ignore


def test_selector_ranks_by_cost_and_health() -> None:
    cheap_model = ModelSpec(
        id="cheap",
        provider="p1",
        provider_model="m1",
        context_length=8000,
        usd_per_1m_input=1.0,
        usd_per_1m_output=1.0,
        capabilities=frozenset([Capability.REASONING]),
    )
    exp_model = ModelSpec(
        id="exp",
        provider="p2",
        provider_model="m2",
        context_length=8000,
        usd_per_1m_input=10.0,
        usd_per_1m_output=10.0,
        capabilities=frozenset([Capability.REASONING]),
    )
    registry = ModelRegistry({cheap_model.id: cheap_model, exp_model.id: exp_model})

    index = CapabilityIndex(registry)
    health = HealthMonitor()

    # Both are healthy, cheap should be ranked first
    selector = ModelSelector(index, health)
    ranked = selector.select(frozenset([Capability.REASONING]), Constraints())

    assert len(ranked) == 2
    assert ranked[0].id == "cheap"
    assert ranked[1].id == "exp"

    # Now make the cheap model's provider unhealthy
    health.record("p1", ok=False, latency_ms=1000)
    health.record("p1", ok=False, latency_ms=1000)
    health.record("p1", ok=False, latency_ms=1000)
    # The breaker opens

    ranked_unhealthy = selector.select(frozenset([Capability.REASONING]), Constraints())

    # Since p1 breaker is open, it might be heavily penalized or excluded depending on selector logic
    # In ModelSelector, open breaker returns 0 score, which forces it to the bottom
    assert ranked_unhealthy[0].id == "exp"
    assert ranked_unhealthy[1].id == "cheap"


# ─── Phase 4: FAST/DEEP tier weighting ────────────────────────────────────


def _fast_local() -> ModelSpec:
    """Cheap, quick, modest quality — the FAST-tier ideal."""
    return ModelSpec(
        id="fast",
        provider="p_fast",
        provider_model="m_fast",
        context_length=8000,
        usd_per_1m_input=0.0,
        usd_per_1m_output=0.0,
        cost_class=CostClass.LOCAL,
        latency_estimate_ms=100,
        capabilities=frozenset([Capability.REASONING]),
        supports_reasoning=False,
        quality_score=0.55,
    )


def _deep_local() -> ModelSpec:
    """Slower and (nominally) costlier but stronger — the DEEP-tier ideal.

    Kept LOCAL so it survives a zero-cost filter; the point of this pair is the
    tier *weighting*, not the policy filter (covered separately below).
    """
    return ModelSpec(
        id="deep",
        provider="p_deep",
        provider_model="m_deep",
        context_length=32000,
        usd_per_1m_input=0.0,
        usd_per_1m_output=0.0,
        cost_class=CostClass.LOCAL,
        latency_estimate_ms=1500,
        capabilities=frozenset([Capability.REASONING]),
        supports_reasoning=True,
        quality_score=0.95,
    )


def _selector_for(*specs: ModelSpec) -> ModelSelector:
    registry = ModelRegistry({s.id: s for s in specs})
    return ModelSelector(CapabilityIndex(registry), HealthMonitor())


def test_fast_tier_prefers_the_quick_cheap_model() -> None:
    selector = _selector_for(_fast_local(), _deep_local())
    ranked = selector.select(
        frozenset([Capability.REASONING]), Constraints(tier=ModelTier.FAST)
    )
    assert ranked[0].id == "fast", "FAST tier must weight latency/cost over quality"


def test_deep_tier_prefers_the_stronger_slower_model() -> None:
    selector = _selector_for(_fast_local(), _deep_local())
    ranked = selector.select(
        frozenset([Capability.REASONING]), Constraints(tier=ModelTier.DEEP)
    )
    assert ranked[0].id == "deep", "DEEP tier must weight quality/reasoning over latency"


def test_tier_only_reweights_it_never_changes_the_candidate_pool() -> None:
    """Both tiers see the SAME eligible models — a tier is a preference, not a
    capability filter. Only the ORDER differs."""
    selector = _selector_for(_fast_local(), _deep_local())
    caps = frozenset([Capability.REASONING])
    fast = selector.select(caps, Constraints(tier=ModelTier.FAST))
    deep = selector.select(caps, Constraints(tier=ModelTier.DEEP))
    assert {m.id for m in fast} == {m.id for m in deep} == {"fast", "deep"}


# ─── Phase 5: preferences never bypass hard policy ────────────────────────


def test_preferred_model_boosts_ranking_among_eligible_models() -> None:
    """A configured tier model outranks a nominally-stronger peer once both are
    eligible — that is the whole point of naming preferred models."""
    strong = _deep_local()  # quality 0.95, id "deep"
    weak = _fast_local()  # quality 0.55, id "fast"
    selector = _selector_for(strong, weak)
    ranked = selector.select(
        frozenset([Capability.REASONING]),
        Constraints(tier=ModelTier.FAST, preferred_models=("deep",)),
    )
    assert ranked[0].id == "deep", "an explicit preference should win among eligible models"


def test_preferred_paid_model_cannot_bypass_zero_cost() -> None:
    """Phase 5's core guarantee: naming a paid model in preferred_models must
    NOT smuggle it past a ZERO_COST profile. The hard filter runs before the
    preference bonus, so the paid model is never even scored."""
    paid = ModelSpec(
        id="paid-flagship",
        provider="cloud",
        provider_model="flagship",
        context_length=200000,
        usd_per_1m_input=15.0,
        usd_per_1m_output=60.0,
        cost_class=CostClass.PAID,
        capabilities=frozenset([Capability.REASONING]),
        supports_reasoning=True,
        quality_score=0.99,
    )
    local = _fast_local()
    selector = _selector_for(paid, local)
    ranked = selector.select(
        frozenset([Capability.REASONING]),
        Constraints(
            tier=ModelTier.DEEP,
            preferred_models=("paid-flagship",),  # operator names the paid model
            cost_policy=CostPolicy.ZERO_COST,
            network_policy=NetworkPolicy.LOCAL_ONLY,
        ),
    )
    ids = [m.id for m in ranked]
    assert "paid-flagship" not in ids, "ZERO_COST must exclude the paid model despite the preference"
    assert ids == ["fast"]
