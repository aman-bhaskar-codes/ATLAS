import pytest

from atlas.capabilities.errors import CapabilityNotFound
from atlas.capabilities.registry.capability import Capability, CapabilityRegistry, CapabilitySpec
from atlas.infra.types import Tier


def test_register_and_get():
    reg = CapabilityRegistry()
    reg.register(CapabilitySpec(capability=Capability.KNOWLEDGE, safety_tool="knowledge",
                                operations=("search",), default_tier=Tier.NOTIFY))
    assert reg.get(Capability.KNOWLEDGE).safety_tool == "knowledge"


def test_unknown_raises():
    with pytest.raises(CapabilityNotFound):
        CapabilityRegistry().get(Capability.EMAIL)


def test_registered_tools_shape():
    reg = CapabilityRegistry()
    reg.register(CapabilitySpec(capability=Capability.EMAIL, safety_tool="email",
                                operations=("read", "send")))
    assert reg.registered_tools() == {"email": ["read", "send"]}
