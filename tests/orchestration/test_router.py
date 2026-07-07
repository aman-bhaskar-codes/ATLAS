from typing import Any

from atlas.infra.ids import CorrelationId
from atlas.infra.types import ModelResponse, ModelTarget
from atlas.orchestration.router import Router
from atlas.orchestration.types import RiskLevel


class FakeGateway:
    def __init__(self, text: str) -> None: self._t = text
    async def complete(self, req: Any) -> ModelResponse:
        return ModelResponse(text=self._t, target=ModelTarget.LOCAL_FAST, model="fake")


async def test_router_parses_capabilities() -> None:
    gw = FakeGateway('{"needs_tools":true,"needs_reasoning":true,"needs_cloud":false,'
                     '"needs_confirmation":true,"max_risk":"high"}')
    caps = await Router(gw).route("delete my temp files", CorrelationId("c"))  # type: ignore[arg-type]
    assert caps.needs_tools and caps.needs_confirmation and caps.max_risk == RiskLevel.HIGH


async def test_router_fails_cautious() -> None:
    gw = FakeGateway("not json")
    caps = await Router(gw).route("do a thing", CorrelationId("c"))  # type: ignore[arg-type]
    assert caps.needs_confirmation and caps.max_risk == RiskLevel.MEDIUM
