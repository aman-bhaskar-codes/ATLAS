from atlas.intelligence.capabilities import Capability
from atlas.intelligence.contracts import ModelSpec
from atlas.intelligence.health.health_monitor import HealthMonitor
from atlas.intelligence.registry.capability_index import CapabilityIndex
from atlas.intelligence.registry.model_registry import ModelRegistry
from atlas.intelligence.selection.selector import Constraints, ModelSelector  # type: ignore


def test_selector_ranks_by_cost_and_health() -> None:
    cheap_model = ModelSpec(
        id="cheap", provider="p1", provider_model="m1",
        context_length=8000, usd_per_1m_input=1.0, usd_per_1m_output=1.0,
        capabilities=frozenset([Capability.REASONING])
    )
    exp_model = ModelSpec(
        id="exp", provider="p2", provider_model="m2",
        context_length=8000, usd_per_1m_input=10.0, usd_per_1m_output=10.0,
        capabilities=frozenset([Capability.REASONING])
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
