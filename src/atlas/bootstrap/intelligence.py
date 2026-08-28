"""Intelligence bootstrap — providers, registry, gateway, cache, embedder.

ZERO-COST-FIRST ADDITIONS:
 - Profile-driven provider registration: only register providers whose
   cost_class is allowed by the active profile.
 - FreeQuotaGovernor: tracks free-tier quotas for Groq/Gemini/OpenRouter.
 - Groq adapter: registered when ATLAS_GROQ_API_KEY is set and profile allows.
 - Provider lifecycle events: bus wired into InferenceRuntime + FallbackEngine.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from atlas.infra.clock import Clock
from atlas.infra.config import AppConfig, Settings
from atlas.infra.db import Database
from atlas.infra.ids import CorrelationId, IdGenerator
from atlas.infra.llm_tracker import LLMCallTracker
from atlas.infra.logging import get_logger
from atlas.infra.profiles import ProfileConfig, resolve_profile
from atlas.infra.types import AuditRecord, NetworkPolicy
from atlas.intelligence.cache import SemanticCache
from atlas.intelligence.contracts import Constraints, Usage
from atlas.intelligence.gateway import ModelGateway
from atlas.intelligence.governance.budget import Budgets
from atlas.intelligence.governance.cost_governor import CostGovernor
from atlas.intelligence.governance.quota_governor import FreeQuotaGovernor, ProviderQuota
from atlas.intelligence.health.health_monitor import HealthMonitor
from atlas.intelligence.observability.telemetry import Telemetry
from atlas.intelligence.providers.openai_compatible import OpenAICompatibleProvider
from atlas.intelligence.registry.capability_index import CapabilityIndex
from atlas.intelligence.registry.model_registry import ModelRegistry
from atlas.intelligence.registry.provider_registry import ProviderRegistry
from atlas.intelligence.runtime.fallback import FallbackEngine
from atlas.intelligence.runtime.inference import InferenceRuntime
from atlas.intelligence.selection.router import CapabilityRouter
from atlas.intelligence.selection.selector import ModelSelector
from atlas.memory.embedder import CloudEmbedder
from atlas.memory.vectorstore import ChromaVectorStore
from atlas.safety.audit import AuditLog

if TYPE_CHECKING:
    from atlas.infra.bus import MessageBus

_log = get_logger("atlas.bootstrap.intelligence")


@dataclass
class IntelligenceComponents:
    gateway: ModelGateway
    embedder: CloudEmbedder
    llm_tracker: LLMCallTracker
    quota_governor: FreeQuotaGovernor
    profile: ProfileConfig
    registry: ModelRegistry


async def build_intelligence(
    *,
    settings: Settings,
    config: AppConfig,
    config_dir: Path,
    db: Database,
    ids: IdGenerator,
    clock: Clock,
    audit: AuditLog,
    bus: object | None = None,  # MessageBus, optional to avoid circular import
) -> IntelligenceComponents:
    """Build intelligence layer: providers, gateway, embedder, tracker.

    Profile-aware: only registers providers allowed by the active profile.
    """

    # ── Resolve operating profile ─────────────────────────────────────
    profile_name = getattr(settings, "profile", "local_free")
    profile = resolve_profile(profile_name)
    _log.info(
        "profile.resolved",
        event_type="lifecycle",
        profile=profile.profile.value,
        cost_policy=profile.cost_policy.value,
        network_policy=profile.network_policy.value,
        allowed_cost_classes=sorted(profile.allowed_cost_classes),
    )

    # ── Telemetry & Health ────────────────────────────────────────────

    async def on_audit_cost(corr: str, provider: str, model_id: str, usage: Usage, latency_ms: int) -> None:
        await audit.record(
            AuditRecord(
                correlation_id=CorrelationId(corr),
                ts=clock.now(),
                actor="intel_platform",
                action="model.call",
                outcome="ok",
                cost_tokens=usage.input_tokens + usage.output_tokens,
                cost_usd=usage.usd,
                payload={"model": model_id, "provider": provider, "latency_ms": latency_ms},
            )
        )

    telemetry = Telemetry(on_audit_cost)
    health = HealthMonitor()

    # ── Budget governor ───────────────────────────────────────────────
    budgets = Budgets(
        daily_usd=profile.daily_usd,
        weekly_usd=profile.weekly_usd,
        monthly_usd=profile.monthly_usd,
        per_task_usd=profile.per_task_usd,
    )
    governor = CostGovernor(spend=audit, budgets=budgets)

    # ── Free quota governor ───────────────────────────────────────────
    quota_governor = FreeQuotaGovernor()
    if profile.enable_quota_governor:
        # Configure quotas for known free-tier providers
        quota_governor.configure(
            "groq", ProviderQuota(daily_requests=1000, daily_tokens=500_000, requests_per_minute=30)
        )
        quota_governor.configure(
            "gemini", ProviderQuota(daily_requests=1500, daily_tokens=1_000_000, requests_per_minute=15)
        )
        quota_governor.configure(
            "openrouter", ProviderQuota(daily_requests=200, daily_tokens=200_000, requests_per_minute=20)
        )
        _log.info("quota_governor.configured", event_type="lifecycle", providers=["groq", "gemini", "openrouter"])

    # ── Provider registration (profile-filtered) ──────────────────────
    provider_registry = ProviderRegistry()

    # No local Ollama provider: the fleet is five OpenRouter free models.
    # OpenRouter registration below is the sole chat provider.

    if profile.allow_cloud:
        # ── Groq (free-tier) ──────────────────────────────────────────
        groq_key = getattr(settings, "groq_api_key", None)
        if groq_key and "free_quota" in profile.allowed_cost_classes:
            try:
                from atlas.intelligence.providers.groq import GroqProvider

                provider_registry.register(
                    GroqProvider(name="groq", api_key=groq_key, timeout_s=config.models.cloud_timeout_s)
                )
                _log.info("provider.registered", event_type="lifecycle", provider="groq", cost_class="free_quota")
            except Exception as exc:
                _log.warning("provider.groq_failed", event_type="lifecycle", error=str(exc))

        # ── Gemini (free-tier) ────────────────────────────────────────
        if settings.gemini_api_key and "free_quota" in profile.allowed_cost_classes:
            try:
                from atlas.intelligence.providers.gemini import GeminiProvider

                provider_registry.register(
                    GeminiProvider(
                        name="gemini",
                        api_key=settings.gemini_api_key,
                        timeout_s=config.models.cloud_timeout_s,
                    )
                )
                _log.info("provider.registered", event_type="lifecycle", provider="gemini", cost_class="free_quota")
            except Exception as exc:
                _log.warning("provider.gemini_failed", event_type="lifecycle", error=str(exc))

        # ── Paid/Hybrid providers (if profile allows paid OR free_quota) ────────
        allow_openrouter = "paid" in profile.allowed_cost_classes or "free_quota" in profile.allowed_cost_classes
        if allow_openrouter and settings.openrouter_api_key:
            provider_registry.register(
                OpenAICompatibleProvider(
                    name="openrouter",
                    base_url="https://openrouter.ai/api/v1",
                    api_key=settings.openrouter_api_key,
                    timeout_s=config.models.cloud_timeout_s,
                )
            )
            _log.info("provider.registered", event_type="lifecycle", provider="openrouter", cost_class="hybrid")

        if "paid" in profile.allowed_cost_classes:
            if settings.anthropic_api_key:
                try:
                    from atlas.intelligence.providers.anthropic import AnthropicProvider

                    provider_registry.register(
                        AnthropicProvider(
                            name="anthropic",
                            api_key=settings.anthropic_api_key,
                            timeout_s=config.models.cloud_timeout_s,
                        )
                    )
                    _log.info("provider.registered", event_type="lifecycle", provider="anthropic")
                except Exception as exc:
                    _log.warning("provider.anthropic_failed", event_type="lifecycle", error=str(exc))

    _log.info(
        "providers.summary",
        event_type="lifecycle",
        registered=provider_registry.names(),
        profile=profile.profile.value,
    )

    # ── Model registry + selection ────────────────────────────────────
    model_registry = ModelRegistry.from_yaml(config_dir / "models.yaml")

    # Startup discovery of ALL free OpenRouter models is gated OFF by default so
    # only the five curated models.yaml ids exist. Opt in via
    # models.sync_openrouter_free=true (leaves openrouter_sync.py available).
    if config.models.sync_openrouter_free and settings.openrouter_api_key:
        try:
            from atlas.intelligence.registry.openrouter_sync import sync_openrouter_free_models

            synced = await sync_openrouter_free_models(model_registry)
            _log.info("openrouter_sync.startup", event_type="lifecycle", synced=synced)
        except ImportError:
            _log.warning("openrouter_sync.unavailable", event_type="lifecycle")
        except Exception as exc:
            _log.warning("openrouter_sync.startup_failed", event_type="lifecycle", error=str(exc))

    capability_index = CapabilityIndex(model_registry)

    # Phase 0: Construct LLMCallTracker before InferenceRuntime so it can be wired in
    llm_tracker = LLMCallTracker(db=db, ids=ids, clock=clock)

    runtime = InferenceRuntime(
        providers=provider_registry,
        health=health,
        governor=governor,
        quota_governor=quota_governor if profile.enable_quota_governor else None,
        telemetry=telemetry,
        model_timeout_s=config.models.cloud_timeout_s,
        tracker=llm_tracker,
        bus=cast("MessageBus | None", bus),
    )
    fallback = FallbackEngine(bus=cast("MessageBus | None", bus))
    cap_router = CapabilityRouter()
    selector = ModelSelector(capability_index, health)

    embedder = CloudEmbedder(
        base_url=settings.embed_base_url,
        api_key=settings.effective_embed_api_key(),
        model=settings.embed_model,
        timeout_s=config.models.cloud_timeout_s,
    )
    cache_vectors = ChromaVectorStore(str(settings.data_dir / "chroma"), collection="atlas_cache")
    semantic_cache = SemanticCache(db, cache_vectors, embedder)

    # ── Phase 4/5: policy-bearing default constraints ─────────────────
    # WHY the gateway holds the profile's policy: legacy callers hand the
    # gateway a bare ModelRequest with no Constraints. Before this, complete()
    # built a fresh Constraints() per call, leaving cost/network/privacy at
    # UNRESTRICTED — so every Zero-Cost-First filter in ModelSelector._passes()
    # was dead on the real path. Carrying the profile's policy on the single
    # egress point means it cannot be dropped at a call site.
    # prefer_local is derived from the network policy, NOT from the reasoning
    # tier: locality is the profile's decision; the tier only trades quality
    # for latency (see gateway._constraints_for).
    default_constraints = Constraints(
        cost_policy=profile.cost_policy,
        network_policy=profile.network_policy,
        privacy_class=profile.default_privacy,
        prefer_local=profile.network_policy in (NetworkPolicy.OFFLINE, NetworkPolicy.LOCAL_ONLY),
    )

    gateway = ModelGateway(
        router=cap_router,
        selector=selector,
        fallback=fallback,
        runtime=runtime,
        cache=semantic_cache,
        default_constraints=default_constraints,
        fast_models=config.models.fast_models,
        deep_models=config.models.deep_models,
    )

    return IntelligenceComponents(
        gateway=gateway,
        embedder=embedder,
        llm_tracker=llm_tracker,
        quota_governor=quota_governor,
        profile=profile,
        registry=model_registry,
    )
