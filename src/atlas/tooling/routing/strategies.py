"""Strategy registry — pluggable execution shapes (Part 3 §10).

A strategy describes HOW a routed task executes (shape, parallelism,
replanning), never WHICH tool. Only strategies with a real use case in the
current repository are registered (§10); the IR accepts more later.

DELEGATE / HANDOFF are intentionally absent: multi-agent delegation is
flag-off in ATLAS today and a handoff runtime does not exist — registering
them would be decoration (§88 principle applied to strategies).
"""

from __future__ import annotations

from dataclasses import dataclass

from atlas.tooling.errors import ToolingError


@dataclass(frozen=True)
class StrategyDefinition:
    strategy_id: str
    description: str
    requirements: tuple[str, ...] = ()  # capability names the strategy needs
    execution_shape: str = "single"  # single | sequence | parallel | dag | iterative | recovery | human
    supports_parallelism: bool = False
    supports_replanning: bool = False
    supports_recovery: bool = True
    #: Static route template id, when one exists for this strategy (§39).
    template: str | None = None


class StrategyRegistry:
    def __init__(self) -> None:
        self._strategies: dict[str, StrategyDefinition] = {}

    def register(self, definition: StrategyDefinition) -> None:
        if definition.strategy_id in self._strategies:
            raise ToolingError(f"strategy {definition.strategy_id!r} is already registered")
        self._strategies[definition.strategy_id] = definition

    def get(self, strategy_id: str) -> StrategyDefinition:
        strategy = self._strategies.get(strategy_id)
        if strategy is None:
            raise ToolingError(f"strategy {strategy_id!r} is not registered")
        return strategy

    def list(self) -> tuple[StrategyDefinition, ...]:
        return tuple(self._strategies.values())

    def __len__(self) -> int:
        return len(self._strategies)


def builtin_strategies() -> list[StrategyDefinition]:
    return [
        StrategyDefinition(
            strategy_id="DIRECT",
            description="One candidate, one step, no orchestration. Trivial lookups.",
            execution_shape="single",
        ),
        StrategyDefinition(
            strategy_id="SINGLE_AGENT",
            description="The existing OTAR reasoning loop over the tool fabric.",
            execution_shape="single",
            supports_replanning=True,
        ),
        StrategyDefinition(
            strategy_id="SEQUENTIAL",
            description="Ordered steps, each depending on the previous output.",
            execution_shape="sequence",
            supports_replanning=True,
        ),
        StrategyDefinition(
            strategy_id="PARALLEL",
            description="Independent read-only steps fan out, then join.",
            execution_shape="parallel",
            supports_parallelism=True,
            supports_replanning=True,
        ),
        StrategyDefinition(
            strategy_id="DAG",
            description="Dependency-graph execution (the existing DagExecutor shape).",
            execution_shape="dag",
            supports_parallelism=True,
            supports_replanning=True,
        ),
        StrategyDefinition(
            strategy_id="RESEARCH",
            description="Bounded research: retrieve → extract → synthesize → cite.",
            requirements=("web_search",),
            execution_shape="sequence",
            supports_parallelism=True,
            supports_replanning=True,
            template="deep_research_default",
        ),
        StrategyDefinition(
            strategy_id="SOFTWARE_ENGINEERING",
            description="Repository work: inspect → edit → run tests → verify.",
            requirements=("editing",),
            execution_shape="iterative",
            supports_replanning=True,
            template="software_engineering_default",
        ),
        StrategyDefinition(
            strategy_id="ITERATIVE",
            description="Act → observe → evaluate progress → repeat, bounded.",
            execution_shape="iterative",
            supports_replanning=True,
        ),
        StrategyDefinition(
            strategy_id="RECOVERY",
            description="A previously failed route re-entering routing (L9).",
            execution_shape="recovery",
            supports_recovery=True,
        ),
        StrategyDefinition(
            strategy_id="HUMAN_REVIEW",
            description="Route outcome is a human decision, not an execution.",
            execution_shape="human",
        ),
    ]


__all__ = ["StrategyDefinition", "StrategyRegistry", "builtin_strategies"]
