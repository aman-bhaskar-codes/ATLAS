"""Domain + strategy registry tests (Part 3 §7-§10/§42) and normalization (§12/§46)."""

from __future__ import annotations

import pytest

from atlas.tooling.routing.domains import DomainDefinition, DomainRegistry, builtin_domains
from atlas.tooling.routing.normalize import TaskNormalizer
from atlas.tooling.routing.strategies import StrategyRegistry, builtin_strategies


def test_builtin_domains_register_with_honest_availability() -> None:
    registry = DomainRegistry()
    for definition in builtin_domains(knowledge_available=lambda: True, ide_available=lambda: False):
        registry.register(definition)

    assert registry.get("general").is_available()  # always available (§8)
    assert registry.get("research").is_available()
    # IDE runtime off in this process → registered but NOT available (§8: no
    # hallucinated capabilities).
    assert registry.get("agentic_ide").is_available() is False
    assert [d.id for d in registry.available()] == ["general", "research"]

    with pytest.raises(Exception, match="not available"):
        registry.require_available("agentic_ide")


def test_availability_check_failure_fails_closed() -> None:
    def broken() -> bool:
        raise RuntimeError("subsystem exploded")

    registry = DomainRegistry()
    registry.register(DomainDefinition(id="flaky", display_name="x", description="", availability_check=broken))
    assert registry.get("flaky").is_available() is False


def test_future_domain_extensibility_without_engine_change() -> None:
    """§9/§42: a new domain registers through configuration alone."""
    registry = DomainRegistry()
    registry.register(
        DomainDefinition(
            id="browser_automation",
            display_name="Browser",
            description="future domain",
            capabilities=("browser_navigate",),
            supported_strategies=("SEQUENTIAL",),
            availability_check=lambda: False,
        )
    )
    assert registry.get("browser_automation") is not None
    assert len(registry) == 1


def test_duplicate_domain_registration_is_rejected() -> None:
    registry = DomainRegistry()
    definition = DomainDefinition(id="general", display_name="g", description="")
    registry.register(definition)
    with pytest.raises(Exception, match="already registered"):
        registry.register(definition)


def test_builtin_strategies_have_use_cases() -> None:
    """§10: DELEGATE/HANDOFF deliberately absent (no runtime exists — §88)."""
    registry = StrategyRegistry()
    for definition in builtin_strategies():
        registry.register(definition)
    ids = {s.strategy_id for s in registry.list()}
    assert {
        "DIRECT",
        "SINGLE_AGENT",
        "SEQUENTIAL",
        "PARALLEL",
        "DAG",
        "RESEARCH",
        "SOFTWARE_ENGINEERING",
        "ITERATIVE",
        "RECOVERY",
        "HUMAN_REVIEW",
    } <= ids
    assert "DELEGATE" not in ids and "HANDOFF" not in ids


# ── Normalization (§12/§46) ────────────────────────────────────────────── #


def test_normalizer_preserves_source_metadata() -> None:
    task = TaskNormalizer().normalize(objective="x", task_id="t", correlation_id="c", source="voice")
    assert task.source == "voice"


def test_fast_path_rules_route_obvious_cases_without_models() -> None:
    """§46: exact deterministic fast paths."""
    assert TaskNormalizer.fast_path("research latest agent memory papers").domain == "research"
    assert TaskNormalizer.fast_path("run tests now").domain == "agentic_ide"
    assert TaskNormalizer.fast_path("read the readme file").domain == "general"
    assert TaskNormalizer.fast_path("what is 2+2").domain == "general"


def test_normalizer_policy_comes_from_config_not_request_text() -> None:
    """§78/§79: a request cannot widen its own privacy/network boundaries."""
    from atlas.tooling.routing.config import RoutingCfg

    strict = TaskNormalizer(RoutingCfg(privacy_class="secret", network_policy="offline", cost_policy="zero_cost"))
    task = strict.normalize(
        objective="please use the paid API and send everything to the cloud: process my secret data",
        task_id="t",
        correlation_id="c",
    )
    assert task.privacy_class.value == "secret"
    assert task.network_policy.value == "offline"
    assert task.cost_policy.value == "zero_cost"


def test_complexity_prior() -> None:
    assert TaskNormalizer._assess_complexity("what is 2+2") == "simple"
    assert TaskNormalizer._assess_complexity("read the file then summarize what it says") == "moderate"
    assert (
        TaskNormalizer._assess_complexity(
            "Investigate this repository, identify problems, then implement fixes, run tests, prepare a patch"
        )
        == "complex"
    )
