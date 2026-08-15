"""Intelligence bootstrap — providers, registry, gateway, cache, embedder."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from atlas.infra.clock import Clock
from atlas.infra.config import AppConfig, Settings
from atlas.infra.db import Database
from atlas.infra.ids import CorrelationId, IdGenerator
from atlas.infra.llm_tracker import LLMCallTracker
from atlas.infra.logging import get_logger
from atlas.infra.types import AuditRecord
from atlas.intelligence.cache import SemanticCache
from atlas.intelligence.contracts import Usage
from atlas.intelligence.gateway import ModelGateway
from atlas.intelligence.governance.budget import Budgets
from atlas.intelligence.governance.cost_governor import CostGovernor
from atlas.intelligence.health.health_monitor import HealthMonitor
from atlas.intelligence.observability.telemetry import Telemetry
from atlas.intelligence.providers.ollama import OllamaProvider
from atlas.intelligence.providers.openai_compatible import OpenAICompatibleProvider
from atlas.intelligence.registry.capability_index import CapabilityIndex
from atlas.intelligence.registry.model_registry import ModelRegistry
from atlas.intelligence.registry.provider_registry import ProviderRegistry
from atlas.intelligence.runtime.fallback import FallbackEngine
from atlas.intelligence.runtime.inference import InferenceRuntime
from atlas.intelligence.selection.router import CapabilityRouter
from atlas.intelligence.selection.selector import ModelSelector
from atlas.memory.embedder import OllamaEmbedder
from atlas.memory.vectorstore import ChromaVectorStore
from atlas.safety.audit import AuditLog

_log = get_logger("atlas.bootstrap.intelligence")


@dataclass
class IntelligenceComponents:
    gateway: ModelGateway
    embedder: OllamaEmbedder
    llm_tracker: LLMCallTracker


async def build_intelligence(
    *,
    settings: Settings,
    config: AppConfig,
    config_dir: Path,
    db: Database,
    ids: IdGenerator,
    clock: Clock,
    audit: AuditLog,
) -> IntelligenceComponents:
    """Build intelligence layer: providers, gateway, embedder, tracker."""

    async def on_audit_cost(
        corr: str, provider: str, model_id: str, usage: Usage, latency_ms: int
    ) -> None:
        await audit.record(AuditRecord(
            correlation_id=CorrelationId(corr), ts=clock.now(), actor="intel_platform",
            action="model.call", outcome="ok",
            cost_tokens=usage.input_tokens + usage.output_tokens,
            cost_usd=usage.usd,
            payload={"model": model_id, "provider": provider, "latency_ms": latency_ms},
        ))

    telemetry = Telemetry(on_audit_cost)
    health = HealthMonitor()

    budgets = Budgets(
        daily_usd=config.models.daily_usd,
        weekly_usd=config.models.weekly_usd,
        monthly_usd=config.models.monthly_usd,
        per_task_usd=config.models.per_task_usd,
    )
    governor = CostGovernor(spend=audit, budgets=budgets)

    provider_registry = ProviderRegistry()
    provider_registry.register(OllamaProvider(settings.ollama_host, config.models.local_timeout_s))

    if config.models.allow_cloud:
        if settings.deepseek_api_key:
            provider_registry.register(OpenAICompatibleProvider(
                name="deepseek", base_url="https://openrouter.ai/api/v1",
                api_key=settings.deepseek_api_key, timeout_s=config.models.cloud_timeout_s,
            ))
        if settings.glm_api_key:
            provider_registry.register(OpenAICompatibleProvider(
                name="glm", base_url="https://openrouter.ai/api/v1",
                api_key=settings.glm_api_key, timeout_s=config.models.cloud_timeout_s,
            ))
        if settings.kimi_api_key:
            provider_registry.register(OpenAICompatibleProvider(
                name="kimi", base_url="https://openrouter.ai/api/v1",
                api_key=settings.kimi_api_key, timeout_s=config.models.cloud_timeout_s,
            ))
        if settings.mimo_api_key:
            provider_registry.register(OpenAICompatibleProvider(
                name="mimo", base_url="https://openrouter.ai/api/v1",
                api_key=settings.mimo_api_key, timeout_s=config.models.cloud_timeout_s,
            ))
        if settings.anthropic_api_key:
            try:
                from atlas.intelligence.providers.anthropic import AnthropicProvider
                provider_registry.register(AnthropicProvider(
                    name="anthropic",
                    api_key=settings.anthropic_api_key,
                    timeout_s=config.models.cloud_timeout_s,
                ))
                _log.info("provider.registered", event_type="lifecycle", provider="anthropic")
            except Exception as exc:
                _log.warning("provider.anthropic_failed", event_type="lifecycle", error=str(exc))
        if settings.gemini_api_key:
            try:
                from atlas.intelligence.providers.gemini import GeminiProvider
                provider_registry.register(GeminiProvider(
                    name="gemini",
                    api_key=settings.gemini_api_key,
                    timeout_s=config.models.cloud_timeout_s,
                ))
                _log.info("provider.registered", event_type="lifecycle", provider="gemini")
            except Exception as exc:
                _log.warning("provider.gemini_failed", event_type="lifecycle", error=str(exc))

    model_registry = ModelRegistry.from_yaml(config_dir / "models.yaml")
    capability_index = CapabilityIndex(model_registry)

    # Phase 0: Construct LLMCallTracker before InferenceRuntime so it can be wired in
    llm_tracker = LLMCallTracker(db=db, ids=ids, clock=clock)

    runtime = InferenceRuntime(
        providers=provider_registry, health=health,
        governor=governor, telemetry=telemetry,
        model_timeout_s=config.models.cloud_timeout_s,
        tracker=llm_tracker,
    )
    fallback = FallbackEngine()
    cap_router = CapabilityRouter()
    selector = ModelSelector(capability_index, health)

    embedder = OllamaEmbedder(settings)
    cache_vectors = ChromaVectorStore(str(settings.data_dir / "chroma"), collection="atlas_cache")
    semantic_cache = SemanticCache(db, cache_vectors, embedder)

    gateway = ModelGateway(
        router=cap_router, selector=selector,
        fallback=fallback, runtime=runtime, cache=semantic_cache,
    )

    return IntelligenceComponents(
        gateway=gateway, embedder=embedder, llm_tracker=llm_tracker,
    )
