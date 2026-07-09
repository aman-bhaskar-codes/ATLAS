import pytest

from atlas.capabilities.dispatcher import CapabilityDispatcher
from atlas.capabilities.errors import CapabilityDenied
from atlas.capabilities.observability.telemetry import CapabilityTelemetry
from atlas.capabilities.providers.base import CapabilityRequest
from atlas.capabilities.registry.capability import Capability, CapabilityRegistry, CapabilitySpec
from atlas.capabilities.registry.health import CapabilityHealth
from atlas.capabilities.registry.provider_registry import ProviderRegistry
from atlas.infra.ids import CorrelationId
from atlas.infra.types import SafetyDecision, Tier
from atlas.safety.engine import DeniedError
from tests.capabilities.fakes import FakeProvider


class AllowSafety:
    async def guard(self, req, tool):
        return await tool.execute(req.args)


class DenySafety:
    async def guard(self, req, tool):
        raise DeniedError(SafetyDecision(decision="deny", tier=Tier.BLOCK, reason="nope"))


def _setup(safety, provider):
    reg = CapabilityRegistry()
    reg.register(CapabilitySpec(capability=Capability.KNOWLEDGE, safety_tool="knowledge",
                                operations=("search",)))
    health = CapabilityHealth()
    preg = ProviderRegistry(health)
    preg.register(provider)
    tele = CapabilityTelemetry(lambda **k: _noop())
    return CapabilityDispatcher(registry=reg, providers=preg, health=health,
                               safety=safety, telemetry=tele)


async def _noop(): ...


async def test_execute_ok_returns_domain_model():
    d = _setup(AllowSafety(), FakeProvider())
    res = await d.execute(CapabilityRequest(capability=Capability.KNOWLEDGE,
                                            operation="search", args={"q": "hi"}),
                          CorrelationId("c"))
    assert res.ok and res.payload.value == "hi" and res.provider == "fake"


async def test_retry_then_success():
    d = _setup(AllowSafety(), FakeProvider(fail_times=2))  # max_attempts=3
    res = await d.execute(CapabilityRequest(capability=Capability.KNOWLEDGE,
                                            operation="search", args={"q": "x"}),
                          CorrelationId("c"))
    assert res.ok


async def test_denial_raises_capability_denied():
    d = _setup(DenySafety(), FakeProvider())
    with pytest.raises(CapabilityDenied):
        await d.execute(CapabilityRequest(capability=Capability.KNOWLEDGE,
                                          operation="search", args={}), CorrelationId("c"))
