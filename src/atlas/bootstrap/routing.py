"""Routing bootstrap — the routing fabric as a composition-root subsystem.

Wires the RoutingEngine from REAL subsystems: the Part-2 catalog for tool
candidates, the domain/strategy registries (availability checked against what
is actually built in THIS process — §8), the judgment cascade (deterministic
always; Jev/LLM rungs only when config enables them, §88), and the route
store. Model/provider routing stays with ModelGateway/CapabilityDispatcher
(§43/§44) — the engine requests capabilities, never concrete models.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from atlas.infra.bus import MessageBus
from atlas.infra.config import AppConfig, RoutingCfg
from atlas.infra.db import Database
from atlas.infra.logging import get_logger
from atlas.tooling.catalog.catalog import ToolCatalog
from atlas.tooling.routing.domains import DomainRegistry, builtin_domains
from atlas.tooling.routing.events import RouteEventPublisher
from atlas.tooling.routing.judgment import (
    DeterministicJudgmentProvider,
    JevClient,
    JevJudgmentProvider,
    JudgmentCascade,
    JudgmentState,
    JudgmentThresholdPolicy,
    LLMJudgmentProvider,
)
from atlas.tooling.routing.models import DecisionRisk
from atlas.tooling.routing.router import RoutingEngine
from atlas.tooling.routing.store import RouteStore
from atlas.tooling.routing.strategies import StrategyRegistry, builtin_strategies

_log = get_logger("atlas.bootstrap.routing")


@dataclass
class RoutingComponents:
    engine: RoutingEngine
    domains: DomainRegistry
    strategies: StrategyRegistry
    thresholds: JudgmentThresholdPolicy


def build_routing(
    *,
    config: AppConfig,
    db: Database,
    catalog: ToolCatalog | None,
    knowledge_available: bool,
    ide_available: bool,
    gateway: object | None = None,
    bus: MessageBus | None = None,
) -> RoutingComponents:
    routing_cfg: RoutingCfg = config.routing

    domains = DomainRegistry()
    for definition in builtin_domains(
        knowledge_available=lambda: knowledge_available,
        ide_available=lambda: ide_available,
    ):
        domains.register(definition)

    strategies = StrategyRegistry()
    for strategy in builtin_strategies():
        strategies.register(strategy)

    # Deterministic rules answer domain/strategy questions for free (§61).
    deterministic = DeterministicJudgmentProvider(
        rules={
            "domain": _domain_rule(domains),
        }
    )

    jev_provider = None
    llm_provider = None
    jev_client = JevClient()
    if routing_cfg.enable_jev:
        jev_provider = JevJudgmentProvider(jev_client)
        _log.info(
            "routing.jev_enabled",
            event_type="lifecycle",
            configured=jev_client.is_configured(),
        )
    if routing_cfg.enable_llm_judgment and gateway is not None:
        llm_provider = LLMJudgmentProvider(gateway)

    cascade = JudgmentCascade(
        deterministic=deterministic,
        jev=jev_provider,
        llm=llm_provider,
        thresholds=JudgmentThresholdPolicy(
            {
                DecisionRisk.LOW_RISK_ROUTING: routing_cfg.thresholds.low_risk_routing,
                DecisionRisk.MEDIUM_RISK: routing_cfg.thresholds.medium_risk,
                DecisionRisk.HIGH_RISK: routing_cfg.thresholds.high_risk,
                DecisionRisk.DESTRUCTIVE: routing_cfg.thresholds.destructive,
            }
        ),
    )

    publisher = RouteEventPublisher(bus)
    from atlas.tooling.routing.normalize import TaskNormalizer

    engine = RoutingEngine(
        config=routing_cfg,
        domains=domains,
        strategies=strategies,
        catalog=catalog,
        cascade=cascade,
        store=RouteStore(db),
        publish=publisher.publish,
        normalizer=TaskNormalizer(routing_cfg),
    )
    components = RoutingComponents(
        engine=engine,
        domains=domains,
        strategies=strategies,
        thresholds=cascade.thresholds,
    )
    _log.info(
        "routing.ready",
        event_type="lifecycle",
        domains=len(domains),
        strategies=len(strategies),
        jev=routing_cfg.enable_jev,
        llm_judgment=routing_cfg.enable_llm_judgment,
    )
    return components


def _domain_rule(domains: DomainRegistry) -> Callable[[JudgmentState], tuple[str, float] | None]:
    """A second-opinion deterministic rule for the judgment cascade: matches a
    domain ONLY when the objective names it unambiguously and it is live."""

    def rule(state: JudgmentState) -> tuple[str, float] | None:
        objective = str(state.data.get("objective", "")).lower()
        for domain in domains.available():
            if domain.id != "general" and domain.id.replace("_", " ") in objective:
                return domain.id, 0.95
        return None

    return rule
