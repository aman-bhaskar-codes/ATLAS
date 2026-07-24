from atlas.capabilities.registry.capability import Capability
from atlas.capabilities.router import CapabilityRouter
from atlas.infra.ids import CorrelationId


class GW:
    async def complete(self, req) -> None:  # type: ignore
        from atlas.infra.types import ModelResponse, ModelTarget
        return ModelResponse(text="[]", target=ModelTarget.LOCAL_FAST, model="f")  # type: ignore


async def test_keyword_signal_routes() -> None:
    caps = await CapabilityRouter(GW()).route("check my email inbox", CorrelationId("c"))  # type: ignore
    assert Capability.EMAIL in caps


async def test_no_signal_empty() -> None:
    caps = await CapabilityRouter(GW()).route("hello there", CorrelationId("c"))  # type: ignore
    assert caps == frozenset()
