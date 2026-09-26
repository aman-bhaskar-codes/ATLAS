"""Candidate discovery + hard filter tests, incl. §84 property invariants."""

from __future__ import annotations

from typing import Any

import pytest

from atlas.tooling.routing.candidates import CandidateDiscovery, HardFilter
from atlas.tooling.routing.models import RouteCandidate, TaskIR
from tests.tooling.catalog_helpers import build_catalog, make_definition, registry_with


def _task(**overrides: Any) -> TaskIR:
    defaults: dict[str, Any] = {
        "task_id": "t",
        "correlation_id": "c",
        "objective": "do the thing",
    }
    defaults.update(overrides)
    return TaskIR(**defaults)


@pytest.mark.asyncio
async def test_discovery_uses_catalog_find_candidates(memory_db: Any) -> None:
    """§18: the router goes through catalog.find_candidates, not storage."""
    from atlas.tooling.models.tool_health import ToolRuntimeState

    registry = registry_with(
        make_definition("filesystem", operations=("read", "write"), description="files"),
        make_definition(
            "knowledge",
            namespace=__import__("atlas.tooling.models.identity", fromlist=["ToolNamespace"]).ToolNamespace.CAPABILITY,
            operations=("search",),
            description="knowledge",
            capability="knowledge",
            network_required=True,
        ),
    )
    for tid in ("native:atlas:filesystem", "capability:atlas:knowledge"):
        registry.set_status(tid, ToolRuntimeState.READY)
    catalog = build_catalog(memory_db, registry)
    await catalog.initialize()

    discovery = CandidateDiscovery(catalog)
    all_candidates = discovery.discover_all(_task())
    assert {c.candidate_id for c in all_candidates} == {
        "native:atlas:filesystem",
        "capability:atlas:knowledge",
    }
    assert all(c.candidate_type.value == "tool" for c in all_candidates)


def _candidate(**overrides: Any) -> RouteCandidate:
    defaults: dict[str, Any] = {
        "candidate_type": "tool",
        "candidate_id": "native:atlas:tool",
        "operations": ("run",),
        "status": "READY",
        "availability": "AVAILABLE",
        "privacy_class": "secret",
        "cost_class": "free",
        "locality": "local",
    }
    defaults.update(overrides)
    return RouteCandidate(**defaults)


def test_hard_filter_property_invariants() -> None:
    """§84: policy-ineligible candidates are NEVER selected, across a sweep
    of task policies."""
    import itertools

    candidates = [
        _candidate(candidate_id="ok"),
        _candidate(candidate_id="disabled", enabled=False),
        _candidate(candidate_id="stale", status="STALE", availability="UNAVAILABLE"),
        _candidate(candidate_id="unavailable", availability="UNAVAILABLE"),
        _candidate(candidate_id="no_op", operations=("other",)),
        _candidate(candidate_id="auth_missing", requires_auth=True, auth_state="MISSING"),
        _candidate(candidate_id="network", network_required=True),
        _candidate(candidate_id="paid", cost_class="paid"),
        _candidate(candidate_id="unknown_trust", trust_level="unknown"),
        _candidate(candidate_id="public_ceiling", privacy_class="public"),
    ]
    filterer = HardFilter()
    for network_policy, cost_policy, privacy_class in itertools.product(
        ("offline", "free_cloud"), ("zero_cost", "free_only"), ("public", "secret")
    ):
        task = _task(network_policy=network_policy, cost_policy=cost_policy, privacy_class=privacy_class)
        survivors, _rejected = filterer.apply(candidates, task, required_operation="run")
        survivor_ids = {c.candidate_id for c in survivors}
        # The invariants, unconditionally:
        assert "disabled" not in survivor_ids
        assert "stale" not in survivor_ids and "unavailable" not in survivor_ids
        assert "no_op" not in survivor_ids  # unsupported operation
        assert "auth_missing" not in survivor_ids
        assert "unknown_trust" not in survivor_ids
        if network_policy == "offline":
            assert "network" not in survivor_ids  # offline never selects network tools
        if cost_policy in ("zero_cost", "free_only"):
            assert "paid" not in survivor_ids  # paid never silently appears
        if privacy_class == "secret":
            assert "public_ceiling" not in survivor_ids  # secret never routes above public ceiling


def test_hard_filter_records_structured_rejections() -> None:
    """§68: rejections are explainable, not silent."""
    survivors, rejected = HardFilter().apply(
        [_candidate(candidate_id="off", enabled=False), _candidate(candidate_id="fine")],
        _task(),
        required_operation="run",
    )
    assert [c.candidate_id for c in survivors] == ["fine"]
    assert len(rejected) == 1
    assert rejected[0].stage == "hard_filter"
    assert "disabled" in rejected[0].reason


def test_offline_task_never_selects_any_network_candidate() -> None:
    """§84 dedicated property: offline routing never selects network-only."""
    candidates = [_candidate(candidate_id=f"net{i}", network_required=True) for i in range(10)]
    survivors, _ = HardFilter().apply(candidates, _task(network_policy="offline"))
    assert survivors == []
