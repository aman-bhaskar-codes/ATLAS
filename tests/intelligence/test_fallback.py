"""Tests for FallbackEngine — ranked-list cascade, budget-abort, provider-switch logic."""

from __future__ import annotations

import pytest

from atlas.intelligence.capabilities import Capability
from atlas.intelligence.contracts import InferenceResponse, ModelSpec, Usage
from atlas.intelligence.errors import (
    BudgetExceededError,
    FallbackError,
    ProviderError,
)
from atlas.intelligence.runtime.fallback import FallbackEngine


def _spec(model_id: str, provider: str = "p1") -> ModelSpec:
    return ModelSpec(
        id=model_id,
        provider=provider,
        provider_model=model_id,
        context_length=4096,
        usd_per_1m_input=1.0,
        usd_per_1m_output=1.0,
        capabilities=frozenset([Capability.REASONING]),
    )


def _ok_response(model_id: str) -> InferenceResponse:
    return InferenceResponse(
        text="ok",
        model_id=model_id,
        provider="p1",
        usage=Usage(input_tokens=10, output_tokens=10, usd=0.001),
    )


@pytest.mark.asyncio
async def test_first_candidate_succeeds() -> None:
    engine = FallbackEngine()
    specs = [_spec("m1"), _spec("m2")]

    async def attempt(spec: ModelSpec) -> InferenceResponse:
        return _ok_response(spec.id)

    resp = await engine.run(specs, attempt)
    assert resp.model_id == "m1"
    assert resp.fell_back is False
    assert resp.attempts == 1


@pytest.mark.asyncio
async def test_falls_back_to_second_on_provider_error() -> None:
    engine = FallbackEngine()
    specs = [_spec("m1"), _spec("m2")]

    async def attempt(spec: ModelSpec) -> InferenceResponse:
        if spec.id == "m1":
            raise ProviderError("m1 down")
        return _ok_response(spec.id)

    resp = await engine.run(specs, attempt)
    assert resp.model_id == "m2"
    assert resp.fell_back is True
    assert resp.attempts == 2


@pytest.mark.asyncio
async def test_all_candidates_fail_raises_fallback_error() -> None:
    engine = FallbackEngine()
    specs = [_spec("m1"), _spec("m2")]

    async def attempt(spec: ModelSpec) -> InferenceResponse:
        raise ProviderError(f"{spec.id} down")

    with pytest.raises(FallbackError):
        await engine.run(specs, attempt)


@pytest.mark.asyncio
async def test_budget_error_stops_chain_immediately() -> None:
    """BudgetExceededError.provider_switch_helps is False — must not try more providers."""
    engine = FallbackEngine()
    specs = [_spec("m1"), _spec("m2"), _spec("m3")]
    calls: list[str] = []

    async def attempt(spec: ModelSpec) -> InferenceResponse:
        calls.append(spec.id)
        raise BudgetExceededError("over budget")

    with pytest.raises(FallbackError):
        await engine.run(specs, attempt)

    # Only the first candidate should have been tried
    assert calls == ["m1"], f"Expected only m1 to be tried, got {calls}"


@pytest.mark.asyncio
async def test_empty_candidates_raises_fallback_error() -> None:
    engine = FallbackEngine()

    async def attempt(spec: ModelSpec) -> InferenceResponse:
        return _ok_response(spec.id)

    with pytest.raises(FallbackError):
        await engine.run([], attempt)


@pytest.mark.asyncio
async def test_third_candidate_succeeds_after_two_failures() -> None:
    engine = FallbackEngine()
    specs = [_spec("m1"), _spec("m2"), _spec("m3")]

    async def attempt(spec: ModelSpec) -> InferenceResponse:
        if spec.id in ("m1", "m2"):
            raise ProviderError(f"{spec.id} down")
        return _ok_response(spec.id)

    resp = await engine.run(specs, attempt)
    assert resp.model_id == "m3"
    assert resp.fell_back is True
    assert resp.attempts == 3
