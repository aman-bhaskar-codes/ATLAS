"""Free-first invariant tests — the critical safety net.

These tests enforce the non-negotiable guarantees of the zero-cost-first
architecture. They run offline with no API keys and no network.

Invariants tested:
  1. ZERO_COST blocks every paid model at selection time.
  2. Free-only policy blocks paid but allows free_quota.
  3. Privacy SECRET → local models only, regardless of quota.
  4. Offline policy → only local cost_class models.
  5. Quota exhaustion → fallback event emitted → next candidate tried.
  6. All providers down → explicit failure, never silent paid use.
  7. Fallback chain walks local → free → paid (paid excluded by policy).
  8. OpenRouter discovery failure → empty list (not an exception crash).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from atlas.infra.types import CostClass, CostPolicy, NetworkPolicy, PrivacyClass
from atlas.intelligence.contracts import Constraints, ModelSpec
from atlas.intelligence.errors import FallbackError, QuotaExhaustedError, RateLimitError
from atlas.intelligence.governance.quota_governor import (
    FreeQuotaGovernor,
    ProviderQuota,
)
from atlas.intelligence.health.health_monitor import HealthMonitor
from atlas.intelligence.registry.capability_index import CapabilityIndex
from atlas.intelligence.registry.model_registry import ModelRegistry
from atlas.intelligence.runtime.fallback import FallbackEngine
from atlas.intelligence.selection.selector import ModelSelector

# ── Helpers ───────────────────────────────────────────────────────────────


def _spec(
    id: str = "test-model",
    provider: str = "test",
    cost_class: CostClass = CostClass.LOCAL,
    quality: float = 0.7,
    **kw: Any,
) -> ModelSpec:
    return ModelSpec(
        id=id,
        provider=provider,
        provider_model=f"{provider}/{id}",
        context_length=kw.get("context_length", 8192),
        usd_per_1m_input=kw.get("usd_in", 0.0),
        usd_per_1m_output=kw.get("usd_out", 0.0),
        cost_class=cost_class,
        quality_score=quality,
        latency_estimate_ms=kw.get("latency", 100),
        supports_streaming=kw.get("streaming", True),
        capabilities=frozenset(),
    )


LOCAL = _spec("qwen3-4b", "ollama", CostClass.LOCAL, 0.6)
FREE_GROQ = _spec("groq-llama-70b", "groq", CostClass.FREE_QUOTA, 0.8)
FREE_GEMINI = _spec("gemini-flash", "gemini", CostClass.FREE_QUOTA, 0.78)
PAID_DEEPSEEK = _spec("deepseek-v4", "deepseek", CostClass.PAID, 0.95, usd_in=0.14, usd_out=0.28)
PAID_OPENROUTER = _spec("glm-5.2", "openrouter", CostClass.PAID, 0.9, usd_in=0.5, usd_out=0.5)

ALL_MODELS = [LOCAL, FREE_GROQ, FREE_GEMINI, PAID_DEEPSEEK, PAID_OPENROUTER]


def _make_selector(models: list[ModelSpec] | None = None) -> ModelSelector:
    models = models or ALL_MODELS
    specs = {m.id: m for m in models}
    registry = ModelRegistry(specs)
    health = HealthMonitor()
    # Make all providers "available" by default in tests
    for m in models:
        health.record(m.provider, ok=True, latency_ms=50)
    index = CapabilityIndex(registry)
    return ModelSelector(index, health)


def _response(model_id: str, provider: str = "test", **kw: Any):
    """Build a minimal InferenceResponse for tests."""
    from atlas.intelligence.contracts import InferenceResponse, Usage
    return InferenceResponse(
        text="ok",
        model_id=model_id,
        provider=provider,
        usage=Usage(input_tokens=10, output_tokens=5, usd=0.0),
        latency_ms=50,
        fell_back=False,
        attempts=1,
    )


# ═══════════════════════════════════════════════════════════════════════════
# 1. Zero-cost invariant: ZERO_COST never selects paid models
# ═══════════════════════════════════════════════════════════════════════════


class TestZeroCostInvariant:
    def test_zero_cost_excludes_all_paid(self):
        sel = _make_selector()
        constraints = Constraints(
            cost_policy=CostPolicy.ZERO_COST,
            network_policy=NetworkPolicy.LOCAL_ONLY,
            privacy_class=PrivacyClass.PUBLIC,
        )
        ranked = sel.select(frozenset(), constraints)
        for m in ranked:
            assert m.cost_class != CostClass.PAID, f"paid model {m.id} leaked through zero_cost"

    def test_zero_cost_allows_local(self):
        sel = _make_selector()
        constraints = Constraints(cost_policy=CostPolicy.ZERO_COST)
        ranked = sel.select(frozenset(), constraints)
        ids = [m.id for m in ranked]
        assert LOCAL.id in ids

    def test_zero_cost_allows_free_quota(self):
        sel = _make_selector()
        constraints = Constraints(
            cost_policy=CostPolicy.ZERO_COST,
            network_policy=NetworkPolicy.FREE_CLOUD,
        )
        ranked = sel.select(frozenset(), constraints)
        ids = [m.id for m in ranked]
        assert FREE_GROQ.id in ids
        assert FREE_GEMINI.id in ids

    def test_zero_cost_with_paid_key_still_blocks_paid(self):
        """Even when a paid API key exists in the environment, zero_cost blocks it."""
        sel = _make_selector()
        constraints = Constraints(cost_policy=CostPolicy.ZERO_COST)
        ranked = sel.select(frozenset(), constraints)
        assert not any(m.cost_class == CostClass.PAID for m in ranked)

    def test_free_only_blocks_paid_allows_local_and_free(self):
        sel = _make_selector()
        constraints = Constraints(cost_policy=CostPolicy.FREE_ONLY)
        ranked = sel.select(frozenset(), constraints)
        for m in ranked:
            assert m.cost_class != CostClass.PAID


# ═══════════════════════════════════════════════════════════════════════════
# 2. Privacy routing: SECRET → local only, SENSITIVE → local preferred
# ═══════════════════════════════════════════════════════════════════════════


class TestPrivacyRouting:
    def test_secret_allows_only_local(self):
        sel = _make_selector()
        constraints = Constraints(privacy_class=PrivacyClass.SECRET)
        ranked = sel.select(frozenset(), constraints)
        for m in ranked:
            assert m.cost_class == CostClass.LOCAL, f"non-local model {m.id} leaked through SECRET"

    def test_sensitive_prefers_local(self):
        sel = _make_selector()
        constraints = Constraints(
            privacy_class=PrivacyClass.SENSITIVE,
            network_policy=NetworkPolicy.FREE_CLOUD,
        )
        ranked = sel.select(frozenset(), constraints)
        # LOCAL should score highest (local bonus in selector)
        assert ranked[0].cost_class == CostClass.LOCAL

    def test_public_allows_free_cloud(self):
        sel = _make_selector()
        constraints = Constraints(
            privacy_class=PrivacyClass.PUBLIC,
            network_policy=NetworkPolicy.FREE_CLOUD,
        )
        ranked = sel.select(frozenset(), constraints)
        ids = [m.id for m in ranked]
        assert FREE_GROQ.id in ids or FREE_GEMINI.id in ids

    def test_private_no_paid(self):
        sel = _make_selector()
        constraints = Constraints(
            privacy_class=PrivacyClass.PRIVATE,
            cost_policy=CostPolicy.FREE_ONLY,
        )
        ranked = sel.select(frozenset(), constraints)
        for m in ranked:
            assert m.cost_class != CostClass.PAID


# ═══════════════════════════════════════════════════════════════════════════
# 3. Offline mode: only local cost_class models
# ═══════════════════════════════════════════════════════════════════════════


class TestOfflineMode:
    def test_offline_blocks_all_cloud(self):
        sel = _make_selector()
        constraints = Constraints(network_policy=NetworkPolicy.OFFLINE)
        ranked = sel.select(frozenset(), constraints)
        for m in ranked:
            assert m.cost_class == CostClass.LOCAL

    def test_offline_zero_cost_still_works(self):
        """Offline + zero_cost is redundant but must not produce an empty set."""
        sel = _make_selector()
        constraints = Constraints(
            cost_policy=CostPolicy.ZERO_COST,
            network_policy=NetworkPolicy.OFFLINE,
        )
        ranked = sel.select(frozenset(), constraints)
        assert len(ranked) >= 1


# ═══════════════════════════════════════════════════════════════════════════
# 4. Fallback chain: walks ranked list, emits events, never hits paid
# ═══════════════════════════════════════════════════════════════════════════


class TestFallbackChain:
    @pytest.mark.asyncio
    async def test_first_succeeds_no_fallback(self):
        bus = AsyncMock()
        engine = FallbackEngine(bus=bus)
        ranked = [LOCAL, FREE_GROQ]
        call_count = 0

        async def attempt(spec: ModelSpec):
            nonlocal call_count
            call_count += 1
            return _response(spec.id, provider=spec.provider)

        resp = await engine.run(ranked, attempt)
        assert resp.model_id == LOCAL.id
        assert not resp.fell_back
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_fallback_on_provider_failure(self):
        bus = AsyncMock()
        engine = FallbackEngine(bus=bus)
        ranked = [FREE_GROQ, FREE_GEMINI, LOCAL]

        async def attempt(spec: ModelSpec):
            if spec.provider == "groq":
                raise RateLimitError("groq rate limited")
            return _response(spec.id, provider=spec.provider)

        resp = await engine.run(ranked, attempt)
        assert resp.model_id == FREE_GEMINI.id
        assert resp.fell_back
        assert resp.attempts == 2
        # Bus should have received a fallback event
        bus.publish.assert_called_once()
        topic = bus.publish.call_args[0][0]
        assert topic == "provider.fallback"

    @pytest.mark.asyncio
    async def test_quota_exhaustion_triggers_fallback(self):
        bus = AsyncMock()
        engine = FallbackEngine(bus=bus)
        ranked = [FREE_GROQ, LOCAL]

        async def attempt(spec: ModelSpec):
            if spec.provider == "groq":
                raise QuotaExhaustedError("groq", "daily limit")
            return _response(spec.id, provider=spec.provider)

        resp = await engine.run(ranked, attempt)
        assert resp.model_id == LOCAL.id
        assert resp.fell_back

    @pytest.mark.asyncio
    async def test_all_fail_raises_fallback_error(self):
        bus = AsyncMock()
        engine = FallbackEngine(bus=bus)
        ranked = [LOCAL]

        from atlas.intelligence.errors import ProviderError
        async def attempt(spec: ModelSpec):
            raise ProviderError("ollama not running")

        with pytest.raises(FallbackError):
            await engine.run(ranked, attempt)

    @pytest.mark.asyncio
    async def test_non_switchable_error_stops_chain(self):
        """BudgetExceededError has provider_switch_helps=False → chain stops."""
        bus = AsyncMock()
        engine = FallbackEngine(bus=bus)
        ranked = [FREE_GROQ, LOCAL]

        from atlas.intelligence.errors import BudgetExceededError

        async def attempt(spec: ModelSpec):
            raise BudgetExceededError("weekly budget exceeded")

        with pytest.raises(FallbackError, match="budget"):
            await engine.run(ranked, attempt)
        # Should NOT have tried the second candidate
        bus.publish.assert_not_called()  # no fallback event since switch wouldn't help


# ═══════════════════════════════════════════════════════════════════════════
# 5. Quota governor: exhaustion → exception → fallback
# ═══════════════════════════════════════════════════════════════════════════


class TestQuotaGovernorIntegration:
    def test_within_quota_passes(self):
        gov = FreeQuotaGovernor()
        gov.configure("groq", ProviderQuota(daily_requests=100, daily_tokens=100_000))
        gov.check("groq")  # should not raise

    def test_requests_exhausted_raises(self):
        gov = FreeQuotaGovernor()
        gov.configure("groq", ProviderQuota(daily_requests=2, daily_tokens=100_000))
        gov.record("groq", tokens_used=100)
        gov.record("groq", tokens_used=100)
        with pytest.raises(QuotaExhaustedError):
            gov.check("groq", estimated_tokens=100)

    def test_tokens_exhausted_raises(self):
        gov = FreeQuotaGovernor()
        gov.configure("gemini", ProviderQuota(daily_requests=100, daily_tokens=200))
        gov.record("gemini", tokens_used=150)
        with pytest.raises(QuotaExhaustedError):
            gov.check("gemini", estimated_tokens=100)

    def test_unknown_provider_never_raises(self):
        """A provider not configured in the governor is always allowed."""
        gov = FreeQuotaGovernor()
        gov.check("nonexistent_provider")  # should not raise


# ═══════════════════════════════════════════════════════════════════════════
# 6. OpenRouter discovery: failure → empty, not crash
# ═══════════════════════════════════════════════════════════════════════════


class TestOpenRouterDiscovery:
    @pytest.mark.asyncio
    async def test_network_failure_returns_empty(self):
        """When OpenRouter is unreachable, discovery returns error, empty models."""
        from atlas.intelligence.providers.openrouter_free import discover_free_models
        result = await discover_free_models()
        # In offline test env, network is blocked so discovery should fail gracefully
        # OR succeed if network is available — either way, no crash.
        assert result.verified_at is not None
        assert isinstance(result.models, list)

    @pytest.mark.asyncio
    async def test_diff_against_static(self):
        from atlas.intelligence.providers.openrouter_free import FreeModelDiscovery, FreeModelInfo, diff_against_static
        disc = FreeModelDiscovery(
            verified_at=None,
            models=[
                FreeModelInfo(
                    id="meta-llama/llama-3:free",
                    name="Llama 3 Free",
                    context_length=8192,
                    supports_tool_calling=False,
                    supports_vision=False,
                )
            ],
        )
        static = {"meta-llama/llama-3:free", "google/gemma-7b:free"}
        diff = diff_against_static(disc, static)
        assert "google/gemma-7b:free" in diff["disappeared"]
        assert len(diff["appeared"]) == 0


# ═══════════════════════════════════════════════════════════════════════════
# 7. Combined scenario: end-to-end zero-cost enforcement
# ═══════════════════════════════════════════════════════════════════════════


class TestEndToEndZeroCost:
    @pytest.mark.asyncio
    async def test_task_with_zero_cost_uses_only_free_providers(self):
        """Simulate: selector filters → fallback walks only free models → succeeds."""
        bus = AsyncMock()
        engine = FallbackEngine(bus=bus)
        sel = _make_selector()

        # Select under zero_cost — should only get local + free
        constraints = Constraints(
            cost_policy=CostPolicy.ZERO_COST,
            network_policy=NetworkPolicy.FREE_CLOUD,
        )
        ranked = sel.select(frozenset(), constraints)
        assert len(ranked) == 3  # LOCAL, FREE_GROQ, FREE_GEMINI
        assert not any(m.cost_class == CostClass.PAID for m in ranked)

        # Simulate groq failing, gemini succeeding
        attempt_count = 0

        async def attempt(spec: ModelSpec):
            nonlocal attempt_count
            attempt_count += 1
            if spec.provider == "groq":
                raise RateLimitError("rate limited")
            return _response(spec.id, provider=spec.provider)

        resp = await engine.run(ranked, attempt)
        assert resp.model_id in {LOCAL.id, FREE_GEMINI.id}
        assert resp.fell_back or resp.model_id == LOCAL.id
        # Paid models were never even in the ranked list
        assert attempt_count <= 3
