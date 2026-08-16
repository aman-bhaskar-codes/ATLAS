from atlas.infra.ids import CorrelationId
from atlas.infra.types import ModelCapability, ModelRequest, ModelResponse, ModelTarget
from atlas.orchestration.planner import Planner
from atlas.orchestration.types import Capabilities


class FakeGateway:
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def complete(self, req: ModelRequest) -> ModelResponse:
        self.requests.append(req)
        return ModelResponse(
            text='{"goal":"test","steps":[]}',
            target=ModelTarget.LOCAL_FAST,
            model="fake",
        )


async def test_planner_requests_planning_and_json_generation() -> None:
    gateway = FakeGateway()

    plan = await Planner(gateway).plan(  # type: ignore[arg-type]
        "test request",
        "test context",
        Capabilities(),
        CorrelationId("c"),
    )

    assert plan.goal == "test"
    assert gateway.requests[0].required_capabilities == frozenset(
        {
            ModelCapability.PLANNING,
            ModelCapability.JSON_GENERATION,
        }
    )
