from __future__ import annotations

from typing import Any

import pytest

from atlas.infra.ids import CorrelationId
from atlas.infra.types import ModelCapability, ModelRequest
from atlas.intelligence.contracts import InferenceRequest, InferenceResponse
from atlas.intelligence.gateway import ModelGateway


class RecordingRouter:
    def __init__(self) -> None:
        self.required_capabilities: frozenset[ModelCapability] | None = None

    def required(self, request: InferenceRequest) -> frozenset[ModelCapability]:
        self.required_capabilities = request.required_capabilities
        return request.required_capabilities


class SingleSelector:
    def select(self, required: object, constraints: object) -> list[object]:
        return [object()]


class DirectFallback:
    async def run(self, ranked: list[object], attempt: Any) -> InferenceResponse:
        return await attempt(ranked[0])


class SuccessfulRuntime:
    async def attempt(self, request: InferenceRequest, spec: object) -> InferenceResponse:
        return InferenceResponse(text="ok", model_id="test-model", provider="test")

    async def close(self) -> None:
        return None


def _gateway(router: RecordingRouter) -> ModelGateway:
    return ModelGateway(
        router=router,  # type: ignore[arg-type]
        selector=SingleSelector(),  # type: ignore[arg-type]
        fallback=DirectFallback(),  # type: ignore[arg-type]
        runtime=SuccessfulRuntime(),  # type: ignore[arg-type]
    )


async def test_complete_rejects_empty_required_capabilities() -> None:
    gateway = _gateway(RecordingRouter())

    with pytest.raises(ValueError, match="required_capabilities must not be empty"):
        await gateway.complete(
            ModelRequest(
                correlation_id=CorrelationId("empty-caps"),
                prompt="hello",
            )
        )


async def test_complete_preserves_explicit_required_capabilities() -> None:
    router = RecordingRouter()
    gateway = _gateway(router)
    capabilities = frozenset({ModelCapability.REASONING, ModelCapability.TOOL_CALLING})

    response = await gateway.complete(
        ModelRequest(
            correlation_id=CorrelationId("explicit-caps"),
            prompt="use a tool",
            required_capabilities=capabilities,
        )
    )

    assert response.text == "ok"
    assert router.required_capabilities == capabilities
