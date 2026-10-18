"""Real runtime deadlines with controlled, cancellation-aware provider adapters."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from atlas.infra.ids import CorrelationId
from atlas.intelligence.contracts import InferenceRequest, ModelSpec, StreamChunk, Usage
from atlas.intelligence.errors import ProviderError
from atlas.intelligence.governance.budget import Budgets
from atlas.intelligence.governance.cost_governor import CostGovernor
from atlas.intelligence.health.health_monitor import HealthMonitor
from atlas.intelligence.observability.telemetry import Telemetry
from atlas.intelligence.providers.base import ProviderCompletion
from atlas.intelligence.registry.provider_registry import ProviderRegistry
from atlas.intelligence.runtime.fallback import FallbackEngine
from atlas.intelligence.runtime.inference import InferenceRuntime
from atlas.intelligence.runtime.streaming import StreamingRuntime


class ControlledProvider:
    name = "controlled"
    is_local = True

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.entered = asyncio.Event()
        self.closed = asyncio.Event()
        self.calls = 0

    async def complete(self, **kwargs: object) -> ProviderCompletion:
        self.calls += 1
        self.entered.set()
        if kwargs["model"] == "fast":
            return ProviderCompletion("ok", Usage(input_tokens=1, output_tokens=1))
        if self.fail:
            raise ProviderError("retryable controlled failure")
        try:
            await asyncio.sleep(10)
        finally:
            self.closed.set()
        return ProviderCompletion("late", Usage())

    async def stream(self, **kwargs: object) -> AsyncIterator[StreamChunk]:
        self.entered.set()
        try:
            yield StreamChunk(delta="first")
            await asyncio.sleep(10)
            yield StreamChunk(delta="late", done=True)
        finally:
            self.closed.set()

    async def health(self) -> bool:
        return True

    async def close(self) -> None:
        pass


class EmptySpend:
    async def cost_today(self) -> float:
        return 0.0

    async def cost_this_week(self) -> float:
        return 0.0

    async def cost_this_month(self) -> float:
        return 0.0


def spec(model: str = "slow") -> ModelSpec:
    return ModelSpec(
        id=model,
        provider="controlled",
        provider_model=model,
        context_length=4096,
        usd_per_1m_input=0,
        usd_per_1m_output=0,
    )


def runtime_fixture(
    provider: ControlledProvider, *, streaming: bool = False
) -> tuple[InferenceRuntime | StreamingRuntime, HealthMonitor, list[Usage]]:
    registry = ProviderRegistry()
    registry.register(provider)
    health = HealthMonitor()
    records: list[Usage] = []

    async def audit(cid: str, provider: str, model: str, usage: Usage, latency: int) -> None:
        records.append(usage)

    runtime_type = StreamingRuntime if streaming else InferenceRuntime
    runtime = runtime_type(
        providers=registry,
        health=health,
        governor=CostGovernor(EmptySpend(), Budgets()),
        telemetry=Telemetry(audit),
        model_timeout_s=0.03,
    )
    return runtime, health, records


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_inference_deadline_covers_provider_and_retry_backoff(fail: bool) -> None:
    provider = ControlledProvider(fail=fail)
    runtime, health, records = runtime_fixture(provider)
    assert isinstance(runtime, InferenceRuntime)
    req = InferenceRequest(correlation_id=CorrelationId("deadline"), messages=[])
    with pytest.raises(ProviderError, match="timed out"):
        await asyncio.wait_for(runtime.attempt(req, spec()), timeout=0.3)
    assert health.reliability("controlled") == 0.0
    assert records == [Usage()]
    assert provider.calls == 1
    if not fail:
        assert provider.closed.is_set()


@pytest.mark.asyncio
async def test_timed_out_inference_can_fall_back_to_next_candidate() -> None:
    runtime, health, records = runtime_fixture(ControlledProvider())
    assert isinstance(runtime, InferenceRuntime)
    req = InferenceRequest(correlation_id=CorrelationId("fallback"), messages=[])

    async def attempt(candidate: ModelSpec):
        return await runtime.attempt(req, candidate)

    result = await asyncio.wait_for(FallbackEngine().run([spec(), spec("fast")], attempt), timeout=0.3)
    assert result.text == "ok" and result.fell_back and result.attempts == 2
    assert health.reliability("controlled") == 0.5
    assert len(records) == 2


@pytest.mark.asyncio
async def test_inference_cancellation_propagates_without_false_health_failure() -> None:
    provider = ControlledProvider()
    runtime, health, records = runtime_fixture(provider)
    assert isinstance(runtime, InferenceRuntime)
    task = asyncio.create_task(
        runtime.attempt(InferenceRequest(correlation_id=CorrelationId("cancel"), messages=[]), spec())
    )
    await provider.entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert provider.closed.is_set()
    assert health.snapshot() == {}
    assert records == []


@pytest.mark.asyncio
async def test_streaming_deadline_closes_provider_and_records_failure() -> None:
    provider = ControlledProvider()
    runtime, health, records = runtime_fixture(provider, streaming=True)
    assert isinstance(runtime, StreamingRuntime)
    req = InferenceRequest(correlation_id=CorrelationId("stream"), messages=[])

    async def consume() -> None:
        async for _ in runtime.attempt(req, spec()):
            pass

    with pytest.raises(ProviderError, match="timed out"):
        await asyncio.wait_for(consume(), timeout=0.3)
    assert provider.closed.is_set()
    assert health.reliability("controlled") == 0.0
    assert records == [Usage()]


@pytest.mark.asyncio
async def test_early_stream_close_closes_provider_without_success_record() -> None:
    provider = ControlledProvider()
    runtime, health, records = runtime_fixture(provider, streaming=True)
    assert isinstance(runtime, StreamingRuntime)
    stream = runtime.attempt(InferenceRequest(correlation_id=CorrelationId("close"), messages=[]), spec())
    assert (await anext(stream)).delta == "first"
    await stream.aclose()
    assert provider.closed.is_set()
    assert health.snapshot() == {}
    assert records == []


@pytest.mark.asyncio
async def test_stream_creation_failure_is_recorded_as_provider_failure() -> None:
    class FailedStreamProvider(ControlledProvider):
        def stream(self, **kwargs: object) -> AsyncIterator[StreamChunk]:
            raise ProviderError("stream creation failed")

    runtime, health, records = runtime_fixture(FailedStreamProvider(), streaming=True)
    assert isinstance(runtime, StreamingRuntime)
    with pytest.raises(ProviderError, match="stream creation failed"):
        async for _ in runtime.attempt(InferenceRequest(correlation_id=CorrelationId("creation"), messages=[]), spec()):
            pass
    assert health.reliability("controlled") == 0.0
    assert records == [Usage()]


@pytest.mark.asyncio
async def test_expired_stream_deadline_prevents_immediately_available_chunk() -> None:
    class ImmediateStreamProvider(ControlledProvider):
        async def stream(self, **kwargs: object) -> AsyncIterator[StreamChunk]:
            try:
                yield StreamChunk(delta="first")
                yield StreamChunk(delta="late", done=True)
            finally:
                self.closed.set()

    provider = ImmediateStreamProvider()
    runtime, health, records = runtime_fixture(provider, streaming=True)
    assert isinstance(runtime, StreamingRuntime)
    stream = runtime.attempt(InferenceRequest(correlation_id=CorrelationId("expired"), messages=[]), spec())
    assert (await anext(stream)).delta == "first"
    # The runtime must not cancel the consumer while suspended at yield.
    await asyncio.sleep(0.05)
    with pytest.raises(ProviderError, match="timed out"):
        await anext(stream)
    assert provider.closed.is_set()
    assert health.reliability("controlled") == 0.0
    assert records == [Usage()]
