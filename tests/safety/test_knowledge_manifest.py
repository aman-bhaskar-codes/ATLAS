"""Knowledge tool ↔ safety manifest alignment.

The funnel is deny-by-default, so an operation the tool accepts but the manifest
never names is a governance hole (and an operation the manifest names but the
tool rejects is dead permission). This test pins both directions, plus the tier
each operation is allowed to carry: indexed-only reads stay AUTO, anything that
leaves the machine is at least NOTIFY.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from atlas.infra.types import Tier
from atlas.tools.research import ResearchTool

CONFIG = Path(__file__).resolve().parents[2] / "config" / "permissions.yaml"

# operation → highest tier it may carry. Read-only == AUTO; outbound == NOTIFY;
# the destructive forget == CONFIRM (raised to DANGEROUS for wide scopes by the
# `mass_research_deletion` matcher, which lives in require_confirm, not here).
EXPECTED: dict[str, Tier] = {
    "search": Tier.AUTO,
    "sources": Tier.AUTO,
    "research": Tier.NOTIFY,
    "deep_research": Tier.NOTIFY,
    "read_url": Tier.NOTIFY,
    "forget": Tier.CONFIRM,
}


def _knowledge_rules() -> dict[str, int]:
    manifest = yaml.safe_load(CONFIG.read_text())
    return {r["operation"]: int(r["tier"]) for r in manifest["rules"] if r.get("tool") == "knowledge"}


def test_every_knowledge_operation_has_an_explicit_tier() -> None:
    rules = _knowledge_rules()
    assert set(rules) == set(EXPECTED), "manifest and tool operations drifted apart"
    for operation, tier in EXPECTED.items():
        assert rules[operation] == int(tier), f"{operation} is tiered {rules[operation]}, expected {int(tier)}"


def test_outbound_operations_are_never_auto_approved() -> None:
    rules = _knowledge_rules()
    # `research` fans out to providers and `read_url` fetches a page: both leave
    # the machine, so neither may be silent.
    assert rules["research"] >= int(Tier.NOTIFY)
    assert rules["read_url"] >= int(Tier.NOTIFY)


def test_the_tool_answers_exactly_the_manifested_operations() -> None:
    class Fabric:
        async def query(self, text: str, *, mode: Any = None, source_types: Any = None) -> Any:
            return object()

    class Runner:
        async def start(self, goal: str, *, resume: bool = True, budget: Any = None, rewrites: Any = ()) -> Any:
            return object()

    class Pipeline:
        async def ingest(self, **kwargs: Any) -> Any:
            return object()

    tool = ResearchTool(fabric=Fabric(), research=Runner(), pipeline=Pipeline())
    assert tool.name == "knowledge"  # the seat permissions.yaml reserves
    for operation in EXPECTED:
        # dry_run recognizing an operation proves the name is wired, and stays
        # network-free while doing it.
        assert "Unknown knowledge operation" not in tool.dry_run({"operation": operation, "query": "q"})
    assert "Unknown knowledge operation" in tool.dry_run({"operation": "not_manifested"})
