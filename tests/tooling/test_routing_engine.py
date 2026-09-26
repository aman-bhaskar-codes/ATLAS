"""RoutingEngine end-to-end tests — the §83 matrix core paths."""

from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio

from atlas.tooling.models.identity import ToolNamespace
from atlas.tooling.routing.config import RoutingCfg
from atlas.tooling.routing.domains import DomainRegistry, builtin_domains
from atlas.tooling.routing.judgment import (
    DeterministicJudgmentProvider,
    JudgmentCascade,
    JudgmentThresholdPolicy,
)
from atlas.tooling.routing.normalize import TaskNormalizer
from atlas.tooling.routing.router import RoutingEngine
from atlas.tooling.routing.store import RouteStore
from atlas.tooling.routing.strategies import StrategyRegistry, builtin_strategies
from tests.tooling.catalog_helpers import build_catalog, make_definition, registry_with


@pytest_asyncio.fixture
async def env(memory_db: Any) -> Any:
    from atlas.tooling.models.tool_health import ToolRuntimeState

    registry = registry_with(
        make_definition(
            "filesystem", operations=("read", "list", "search", "write"), description="read and write files"
        ),
        make_definition("shell", operations=("read_only", "side_effect"), description="run allowlisted shell commands"),
        make_definition(
            "knowledge",
            operations=("search", "research", "read_url"),
            description="web and academic research",
            network_required=True,
        ),
        make_definition(
            "weather",
            namespace=ToolNamespace.CAPABILITY,
            operations=("forecast",),
            description="weather forecast for a location",
            capability="weather",
            network_required=True,
        ),
    )
    for tid in (
        "native:atlas:filesystem",
        "native:atlas:shell",
        "native:atlas:knowledge",
        "capability:atlas:weather",
    ):
        registry.set_status(tid, ToolRuntimeState.READY)
    catalog = build_catalog(memory_db, registry)
    await catalog.initialize()
    return catalog


def _engine(catalog: Any, *, ide_available: bool = True, config: RoutingCfg | None = None) -> RoutingEngine:
    domains = DomainRegistry()
    for definition in builtin_domains(knowledge_available=lambda: True, ide_available=lambda: ide_available):
        domains.register(definition)
    strategies = StrategyRegistry()
    for definition in builtin_strategies():
        strategies.register(definition)
    return RoutingEngine(
        config=config or RoutingCfg(),
        domains=domains,
        strategies=strategies,
        catalog=catalog,
        cascade=JudgmentCascade(
            deterministic=DeterministicJudgmentProvider(
                rules={
                    "domain": lambda state: (
                        ("research", 0.95) if "memory" in str(state.data.get("objective", "")) else None
                    )
                }
            )
        ),
        store=RouteStore(catalog._store._db),
        normalizer=TaskNormalizer(),
    )


@pytest.mark.asyncio
async def test_simple_direct_task_routes_general(env: Any) -> None:
    """§83: simple direct task; §46: no model calls on the fast path."""
    engine = _engine(env)
    task = engine.normalize_request(objective="read the readme file", task_id="t1", correlation_id="c1")
    result = await engine.route(task)
    d = result.decision
    assert d.decision_type == "route"
    assert d.domain == "general"
    assert d.strategy == "DIRECT"
    assert d.selected_candidate is not None
    assert result.plan is not None and result.graph.has_terminal()
    assert d.deterministic_certainty == 1.0  # fast-path rule decided


@pytest.mark.asyncio
async def test_research_task_routes_research_domain_with_candidates(env: Any) -> None:
    """§83: research task → research domain, RESEARCH strategy, catalog
    candidates, fallbacks, plan + graph, persisted, replayable."""
    engine = _engine(env)
    task = engine.normalize_request(
        objective="Research latest papers on agent memory and compare approaches",
        task_id="t2",
        correlation_id="c2",
    )
    result = await engine.route(task, capability="web_search")
    d = result.decision
    assert d.domain == "research"
    assert d.strategy == "RESEARCH"
    assert d.selected_candidate is not None
    assert d.selected_candidate in d.filtered_candidates
    assert len(d.fallback_candidates) >= 1
    assert result.plan is not None
    assert result.plan.template == "deep_research_default"  # §39 static template
    assert result.graph.has_terminal()

    # §55: persisted; §67: replay reconstructs the same selection.
    payload = await engine.inspect_route(d.route_id)
    assert payload is not None and payload["decision"]["route_id"] == d.route_id
    replayed = await engine.replay(d.route_id)
    assert replayed is not None
    assert replayed.decision.selected_candidate == d.selected_candidate


@pytest.mark.asyncio
async def test_ide_task_routes_to_ide_when_available(env: Any) -> None:
    engine = _engine(env, ide_available=True)
    task = engine.normalize_request(
        objective="Fix the failing tests in this repository", task_id="t3", correlation_id="c3"
    )
    result = await engine.route(task)
    assert result.decision.domain == "agentic_ide"
    assert result.decision.strategy in ("SOFTWARE_ENGINEERING", "ITERATIVE")


@pytest.mark.asyncio
async def test_unavailable_domain_falls_back_to_general(env: Any) -> None:
    """§83: known domain unavailable → routing still succeeds via the
    always-available general domain (§8: no hallucinated capabilities)."""
    engine = _engine(env, ide_available=False)
    task = engine.normalize_request(
        objective="Fix the failing tests in this repository", task_id="t4", correlation_id="c4"
    )
    result = await engine.route(task)
    assert result.decision.domain == "general"
    reasons = " ".join(result.decision.reasons)
    assert "unavailable" in reasons


@pytest.mark.asyncio
async def test_no_eligible_candidates_yields_no_route(env: Any) -> None:
    """§83/§64: nothing survives the hard filter → no_route, never a fake pick."""
    engine = _engine(env)
    task = engine.normalize_request(objective="read the readme file", task_id="t5", correlation_id="c5")
    result = await engine.route(task, operation="nonexistent_operation")
    assert result.decision.decision_type == "no_route"
    assert result.plan is None
    assert result.decision.selected_candidate is None


@pytest.mark.asyncio
async def test_judgment_conflict_deterministic_rule_wins(env: Any) -> None:
    """§83: Jev says X, deterministic rule says Y → rule wins (§61/§31)."""

    class ContrarianJev:
        name = "jev"

        async def available(self) -> bool:
            return True

        async def ask(self, questions: Any, state: Any) -> dict[str, Any]:
            return {q.question_id: self._res(q) for q in questions}

        def _res(self, q: Any) -> Any:
            from atlas.tooling.routing.models import JudgmentResult

            return JudgmentResult(
                question_id=q.question_id,
                provider="jev",
                choice="general",
                score=0.99,
                accepted=False,
            )

    config = RoutingCfg(enable_jev=True)
    engine = _engine(env, config=config)
    engine._cascade = JudgmentCascade(
        deterministic=engine._cascade._deterministic,
        jev=ContrarianJev(),
        thresholds=JudgmentThresholdPolicy(),
    )
    task = engine.normalize_request(
        objective="Research latest papers on agent memory and compare approaches",
        task_id="t6",
        correlation_id="c6",
    )
    result = await engine.route(task)
    assert result.decision.domain == "research"  # the deterministic rule, not Jev


@pytest.mark.asyncio
async def test_jev_judgment_resolves_ambiguous_domain(env: Any) -> None:
    """§83: no deterministic rule → Jev (enabled) decides with confidence."""

    class AgreeableJev:
        name = "jev"

        async def available(self) -> bool:
            return True

        async def ask(self, questions: Any, state: Any) -> dict[str, Any]:
            from atlas.tooling.routing.models import JudgmentResult

            return {
                q.question_id: JudgmentResult(
                    question_id=q.question_id,
                    provider="jev",
                    choice="research",
                    score=0.92,
                    raw_probabilities={"research": 0.92},
                )
                for q in questions
            }

    engine = _engine(env, config=RoutingCfg(enable_jev=True))
    engine._cascade = JudgmentCascade(
        deterministic=DeterministicJudgmentProvider(rules={}),
        jev=AgreeableJev(),
        thresholds=JudgmentThresholdPolicy(),
    )
    task = engine.normalize_request(objective="something genuinely ambiguous here", task_id="t7", correlation_id="c7")
    result = await engine.route(task)
    d = result.decision
    assert d.domain == "research"
    assert d.judgment_provider == "jev"
    assert d.judgment_confidence == pytest.approx(0.92)
    assert d.confidence_level.value == "HIGH_CONFIDENCE"


@pytest.mark.asyncio
async def test_ambiguity_without_providers_escalates_to_safe_default(env: Any) -> None:
    """§83/§64: ambiguous + no judgment providers → safe default general,
    explicitly recorded; not a crash, not a fake specialist."""
    engine = _engine(env)
    task = engine.normalize_request(objective="help me plan a birthday party", task_id="t8", correlation_id="c8")
    result = await engine.route(task)
    assert result.decision.domain == "general"
    assert any("safe default" in r for r in result.decision.reasons)


@pytest.mark.asyncio
async def test_cross_domain_plan_compiles_and_validates(env: Any) -> None:
    """§73/§74: explicit cross-domain handoff via plan steps with domains +
    dependencies; the graph represents fan-in through JOIN-free dependencies."""
    from atlas.tooling.routing.models import RouteStep

    engine = _engine(env)
    task = engine.normalize_request(objective="read the readme file", task_id="t9", correlation_id="c9")
    result = await engine.route(task)
    decision = result.decision
    assert decision.selected_candidate is not None

    research_step = RouteStep(step_id="s-research", candidate_id="native:atlas:knowledge", domain="research")
    impl_step = RouteStep(
        step_id="s-impl", candidate_id="native:atlas:filesystem", domain="general", depends_on=("s-research",)
    )
    plan = engine._planner.compile(decision, extra_steps=(research_step, impl_step))
    graph = engine._planner.build_graph(plan)
    known = {c.candidate_id: c for c in decision.candidates}
    validation = engine._planner.validate(plan, graph, known)
    assert validation.ok, validation.problems
    domains_in_plan = {s.domain for s in plan.steps if s.domain}
    assert {"research", "general"} <= domains_in_plan


@pytest.mark.asyncio
async def test_route_events_emitted_through_lifecycle(env: Any) -> None:
    """§56: structured route events across the lifecycle."""
    events: list[tuple[str, dict[str, Any]]] = []
    engine = _engine(env)

    async def collect(kind: str, payload: dict[str, Any]) -> None:
        events.append((kind, payload))

    engine._publish = collect
    task = engine.normalize_request(objective="read the readme file", task_id="t10", correlation_id="c10")
    await engine.route(task)
    kinds = [k for k, _ in events]
    assert "route.started" in kinds
    assert "route.domain_selected" in kinds
    assert "route.strategy_selected" in kinds
    assert "route.capabilities_resolved" in kinds
    assert "route.candidates_discovered" in kinds
    assert "route.candidates_filtered" in kinds
    assert "route.compiled" in kinds
    assert "route.completed" in kinds


@pytest.mark.asyncio
async def test_explanation_covers_layers(env: Any) -> None:
    """§68: structured reasons — why this domain/strategy/tool, what was rejected."""
    engine = _engine(env)
    task = engine.normalize_request(objective="read the readme file", task_id="t11", correlation_id="c11")
    result = await engine.route(task)
    explanation = await engine.explain(result.decision.route_id)
    assert explanation is not None
    assert len(explanation["reasons"]) >= 4
    assert explanation["domain"] == "general"
    assert "score_components" in explanation
    assert explanation["confidence"]["deterministic_certainty"] == 1.0
