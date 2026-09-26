"""M0.4 — tool-router hardening + Tool-RAG.

Proves `ToolRouter.rank` now honors intent (lexical relevance can reorder tools
past their metadata/health ranking) and that `shortlist_specs` returns a bounded,
relevance-ranked ToolCallSpec set — the toolset the agent engine advertises.
Backward-compat of the no-intent ranking lives in test_tool_runtime.py.
"""

from __future__ import annotations

from typing import Any

from atlas.infra.types import ToolCallSpec
from atlas.orchestration.registry import ToolMetadata, ToolRegistry
from atlas.orchestration.tool_routing import ToolHealthTracker, ToolRouter


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name

    async def execute(self, args: dict[str, Any]) -> Any:  # pragma: no cover - never called
        raise NotImplementedError


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    # metadata-favored: idempotent, no side effects
    reg.register(
        _FakeTool("notes"),
        ("list",),
        ToolMetadata(name="notes", operations=("list",), description="list notes", idempotent=True, side_effects=False),
    )
    # metadata-penalized: non-idempotent, side-effecting
    reg.register(
        _FakeTool("shell"),
        ("run",),
        ToolMetadata(
            name="shell",
            operations=("run",),
            description="execute shell commands",
            idempotent=False,
            side_effects=True,
        ),
    )
    return reg


def _router() -> ToolRouter:
    return ToolRouter(_registry(), ToolHealthTracker())


def test_no_intent_ranks_by_metadata() -> None:
    ranked = _router().rank()
    assert ranked.index("notes") < ranked.index("shell")


def test_intent_relevance_reorders_past_metadata() -> None:
    # "shell" is metadata-penalized but a direct keyword hit; relevance wins.
    ranked = _router().rank("run shell")
    assert ranked[0] == "shell"


def test_irrelevant_intent_leaves_metadata_order() -> None:
    ranked = _router().rank("weather forecast tomorrow")
    assert ranked.index("notes") < ranked.index("shell")


def test_affinity_scores_name_and_ops_over_description() -> None:
    intent = {"run"}  # matches shell's operation
    strong = ToolRouter._affinity(intent, "shell", _registry().metadata("shell"))
    weak = ToolRouter._affinity({"commands"}, "shell", _registry().metadata("shell"))  # description-only
    assert strong == 1.0
    assert 0.0 < weak < 1.0


def test_shortlist_specs_is_bounded_and_ranked() -> None:
    router = _router()
    specs = router.shortlist_specs("run shell", max_tools=1)
    assert len(specs) == 1
    assert isinstance(specs[0], ToolCallSpec)
    assert specs[0].name == "shell"  # top of the intent-ranked list


def test_shortlist_specs_returns_specs_in_rank_order() -> None:
    router = _router()
    ranked = router.rank("run shell")
    specs = router.shortlist_specs("run shell")
    assert [s.name for s in specs] == ranked  # both tools, rank order preserved


def test_shortlist_specs_empty_registry() -> None:
    router = ToolRouter(ToolRegistry(), ToolHealthTracker())
    assert router.shortlist_specs("anything") == ()
