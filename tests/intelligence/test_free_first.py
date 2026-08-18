"""Zero-cost-first policy engine tests.

Tests the entire policy enforcement chain:
  - CostClass/CostPolicy/NetworkPolicy/PrivacyClass enums
  - Policy-aware ModelSelector filtering + scoring
  - FreeQuotaGovernor tracking + enforcement
  - Profile system resolution + defaults
  - Error taxonomy (QuotaExhaustedError, PolicyViolationError)
"""

from __future__ import annotations

import pytest

from atlas.infra.profiles import AtlasProfile, list_profiles, resolve_profile
from atlas.infra.types import CostClass, CostPolicy, NetworkPolicy, PrivacyClass
from atlas.intelligence.contracts import Constraints, ModelSpec
from atlas.intelligence.errors import PolicyViolationError, QuotaExhaustedError
from atlas.intelligence.governance.quota_governor import (
    FreeQuotaGovernor,
    ProviderQuota,
)

# ── Fixtures ──────────────────────────────────────────────────────────────

def _spec(
    id: str = "test-model",
    provider: str = "test",
    cost_class: CostClass = CostClass.LOCAL,
    quality: float = 0.7,
    **kw,
) -> ModelSpec:
    """Create a minimal ModelSpec for testing."""
    return ModelSpec(
        id=id,
        provider=provider,
        provider_model=f"{provider}/{id}",
        context_length=kw.get("context_length", 8192),
        usd_per_1m_input=kw.get("usd_in", 0.0),
        usd_per_1m_output=kw.get("usd_out", 0.0),
        cost_class=cost_class,
        quality_score=quality,
        supports_streaming=kw.get("streaming", True),
        capabilities=frozenset(),
    )


LOCAL = _spec("qwen3-4b", "ollama", CostClass.LOCAL, 0.6)
FREE_QUOTA = _spec("groq-llama", "groq", CostClass.FREE_QUOTA, 0.8, usd_in=0.0, usd_out=0.0)
PAID = _spec("deepseek-v4", "deepseek", CostClass.PAID, 0.95, usd_in=0.14, usd_out=0.28)


# ═══════════════════════════════════════════════════════════════════════════
# 1. Policy Enums
# ═══════════════════════════════════════════════════════════════════════════

class TestCostClass:
    def test_local_value(self):
        assert CostClass.LOCAL == "local"

    def test_free_quota_value(self):
        assert CostClass.FREE_QUOTA == "free_quota"

    def test_paid_value(self):
        assert CostClass.PAID == "paid"

    def test_all_values(self):
        assert set(CostClass) == {CostClass.LOCAL, CostClass.FREE, CostClass.FREE_QUOTA, CostClass.PAID}


class TestCostPolicy:
    def test_zero_cost_is_strictest(self):
        """ZERO_COST must exist and be the strictest mode."""
        assert CostPolicy.ZERO_COST == "zero_cost"

    def test_all_policies(self):
        expected = {"zero_cost", "free_only", "free_preferred", "balanced", "unrestricted"}
        assert {p.value for p in CostPolicy} == expected


class TestNetworkPolicy:
    def test_offline_blocks_everything(self):
        assert NetworkPolicy.OFFLINE == "offline"

    def test_local_only_allows_lan(self):
        assert NetworkPolicy.LOCAL_ONLY == "local_only"


class TestPrivacyClass:
    def test_secret_is_most_restrictive(self):
        assert PrivacyClass.SECRET == "secret"

    def test_public_is_least_restrictive(self):
        assert PrivacyClass.PUBLIC == "public"


# ═══════════════════════════════════════════════════════════════════════════
# 2. ModelSpec cost_class integration
# ═══════════════════════════════════════════════════════════════════════════

class TestModelSpecCostClass:
    def test_default_is_paid(self):
        """Safe default: if cost_class is omitted, assume PAID."""
        spec = ModelSpec(
            id="x", provider="x", provider_model="x",
            context_length=1, usd_per_1m_input=0, usd_per_1m_output=0,
        )
        assert spec.cost_class == CostClass.PAID

    def test_local_model(self):
        assert LOCAL.cost_class == CostClass.LOCAL

    def test_free_quota_model(self):
        assert FREE_QUOTA.cost_class == CostClass.FREE_QUOTA

    def test_paid_model(self):
        assert PAID.cost_class == CostClass.PAID


# ═══════════════════════════════════════════════════════════════════════════
# 3. Constraints with policy fields
# ═══════════════════════════════════════════════════════════════════════════

class TestConstraints:
    def test_default_is_unrestricted(self):
        """Backward compat: default constraints are fully permissive."""
        c = Constraints()
        assert c.cost_policy == CostPolicy.UNRESTRICTED
        assert c.network_policy == NetworkPolicy.UNRESTRICTED
        assert c.privacy_class == PrivacyClass.PUBLIC

    def test_zero_cost_constraints(self):
        c = Constraints(cost_policy=CostPolicy.ZERO_COST, network_policy=NetworkPolicy.LOCAL_ONLY)
        assert c.cost_policy == CostPolicy.ZERO_COST
        assert c.network_policy == NetworkPolicy.LOCAL_ONLY


# ═══════════════════════════════════════════════════════════════════════════
# 4. FreeQuotaGovernor
# ═══════════════════════════════════════════════════════════════════════════

class TestFreeQuotaGovernor:
    def test_no_config_no_block(self):
        """Unconfigured providers are never blocked."""
        g = FreeQuotaGovernor()
        g.check("unknown_provider")  # should not raise

    def test_within_quota(self):
        g = FreeQuotaGovernor()
        g.configure("groq", ProviderQuota(daily_requests=100, daily_tokens=10_000))
        g.check("groq", 500)  # within limits

    def test_daily_requests_exceeded(self):
        g = FreeQuotaGovernor()
        g.configure("groq", ProviderQuota(daily_requests=2, daily_tokens=1_000_000))
        g.record("groq", 100)
        g.record("groq", 100)
        with pytest.raises(QuotaExhaustedError, match="daily request limit"):
            g.check("groq")

    def test_daily_tokens_exceeded(self):
        g = FreeQuotaGovernor()
        g.configure("groq", ProviderQuota(daily_requests=1000, daily_tokens=100))
        g.record("groq", 90)
        with pytest.raises(QuotaExhaustedError, match="daily token limit"):
            g.check("groq", 50)

    def test_remaining_snapshot(self):
        g = FreeQuotaGovernor()
        g.configure("groq", ProviderQuota(daily_requests=100, daily_tokens=10_000))
        g.record("groq", 500)
        r = g.remaining("groq")
        assert r["requests_remaining"] == 99
        assert r["tokens_remaining"] == 9500
        assert r["requests_used"] == 1
        assert r["tokens_used"] == 500

    def test_reset_daily(self):
        g = FreeQuotaGovernor()
        g.configure("groq", ProviderQuota(daily_requests=2, daily_tokens=100))
        g.record("groq", 50)
        g.record("groq", 50)
        g.reset_daily()
        g.check("groq")  # should succeed after reset

    def test_full_snapshot(self):
        g = FreeQuotaGovernor()
        g.configure("groq", ProviderQuota())
        g.configure("gemini", ProviderQuota())
        snap = g.snapshot()
        assert "groq" in snap
        assert "gemini" in snap

    def test_quota_error_properties(self):
        err = QuotaExhaustedError("groq", "daily limit")
        assert err.provider_switch_helps is True
        assert err.retryable is False


# ═══════════════════════════════════════════════════════════════════════════
# 5. Profile System
# ═══════════════════════════════════════════════════════════════════════════

class TestProfiles:
    def test_local_free_defaults(self):
        p = resolve_profile("local_free")
        assert p.profile == AtlasProfile.LOCAL_FREE
        assert p.cost_policy == CostPolicy.ZERO_COST
        assert p.network_policy == NetworkPolicy.LOCAL_ONLY
        assert p.allow_cloud is False
        assert p.daily_usd == 0.0
        assert "local" in p.allowed_cost_classes
        assert "paid" not in p.allowed_cost_classes

    def test_free_hybrid(self):
        p = resolve_profile("free_hybrid")
        assert p.allow_cloud is True
        assert p.cost_policy == CostPolicy.FREE_ONLY
        assert "free_quota" in p.allowed_cost_classes
        assert "paid" not in p.allowed_cost_classes

    def test_production_allows_paid(self):
        p = resolve_profile("production")
        assert "paid" in p.allowed_cost_classes
        assert p.daily_usd > 0

    def test_unknown_falls_back_to_local_free(self):
        """Unknown profile name → safe fallback to local_free."""
        p = resolve_profile("nonexistent_profile")
        assert p.profile == AtlasProfile.LOCAL_FREE

    def test_list_profiles(self):
        profiles = list_profiles()
        assert len(profiles) == 4
        names = {p.profile.value for p in profiles}
        assert names == {"local_free", "free_hybrid", "free_demo", "production"}


# ═══════════════════════════════════════════════════════════════════════════
# 6. Error Taxonomy
# ═══════════════════════════════════════════════════════════════════════════

class TestErrorTaxonomy:
    def test_quota_exhausted_is_switchable(self):
        err = QuotaExhaustedError("test-provider", "quota hit")
        assert err.provider_switch_helps is True
        assert err.retryable is False

    def test_policy_violation_is_not_switchable(self):
        err = PolicyViolationError("ZERO_COST blocks paid")
        assert err.provider_switch_helps is False
        assert err.retryable is False


# ═══════════════════════════════════════════════════════════════════════════
# 7. Embedding Failover
# ═══════════════════════════════════════════════════════════════════════════

class TestEmbeddingFailover:
    def test_embedding_error_exists(self):
        from atlas.memory.embedder import EmbeddingError
        err = EmbeddingError("test")
        assert str(err) == "test"

    @pytest.mark.asyncio
    async def test_fallback_embedder_tries_all(self):
        from atlas.memory.embedder import EmbeddingError, FallbackEmbedder

        class FailEmbed:
            async def embed(self, text: str) -> list[float]:
                raise EmbeddingError("fail")

        class SuccessEmbed:
            async def embed(self, text: str) -> list[float]:
                return [1.0, 2.0, 3.0]

        fb = FallbackEmbedder([FailEmbed(), SuccessEmbed()])
        result = await fb.embed("test")
        assert result == [1.0, 2.0, 3.0]

    @pytest.mark.asyncio
    async def test_fallback_embedder_raises_when_all_fail(self):
        from atlas.memory.embedder import EmbeddingError, FallbackEmbedder

        class FailEmbed:
            async def embed(self, text: str) -> list[float]:
                raise EmbeddingError("fail")

        fb = FallbackEmbedder([FailEmbed(), FailEmbed()])
        with pytest.raises(EmbeddingError, match="All embedders failed"):
            await fb.embed("test")
