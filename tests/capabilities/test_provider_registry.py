import pytest

from atlas.capabilities.errors import NoProviderAvailable
from atlas.capabilities.registry.health import CapabilityHealth
from atlas.capabilities.registry.provider_registry import ProviderRegistry
from tests.capabilities.fakes import FakeProvider


def test_ranks_by_preference() -> None:
    h = CapabilityHealth()
    reg = ProviderRegistry(h)
    reg.register(FakeProvider("b"), preference=200)
    reg.register(FakeProvider("a"), preference=10)
    assert next(p.name for p in reg.candidates(FakeProvider().capability)) == "a"


def test_unhealthy_dropped() -> None:
    h = CapabilityHealth()
    reg = ProviderRegistry(h)
    p = FakeProvider("x")
    reg.register(p)
    for _ in range(10):
        h.record("x", ok=False, latency_ms=1)   # trip breaker
    with pytest.raises(NoProviderAvailable):
        reg.candidates(p.capability)
