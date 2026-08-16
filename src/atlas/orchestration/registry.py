"""Tool registry — the tool-agnostic lookup seam.

WHY: the orchestrator gets tool NAMES from plans/actions and must resolve them
without importing concrete tools. The registry is the only thing that holds tool
instances; the dispatcher asks it by name. catalog() feeds the prompt so the
model knows what's available.

ToolMetadata lets the planner and ToolRouter make informed decisions
(cost/latency/idempotency/side-effects) without executing anything.
"""

from __future__ import annotations

from dataclasses import dataclass

from atlas.tools.base import Tool


@dataclass(frozen=True)
class ToolMetadata:
    """Static, declared facts about a tool. Health is live; this is not."""

    name: str
    description: str = ""
    operations: tuple[str, ...] = ()
    safety_tool: str | None = None  # manifest tool name for tier lookup
    estimated_cost_usd: float = 0.0  # per typical call
    estimated_latency_ms: int = 500  # p50 prior
    idempotent: bool = True
    side_effects: bool = False
    supports_rollback: bool = False


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._operations: dict[str, tuple[str, ...]] = {}
        self._metadata: dict[str, ToolMetadata] = {}

    def register(
        self,
        tool: Tool,
        operations: tuple[str, ...],
        metadata: ToolMetadata | None = None,
    ) -> None:
        self._tools[tool.name] = tool
        self._operations[tool.name] = operations
        if metadata is not None:
            self._metadata[tool.name] = metadata

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def metadata(self, name: str) -> ToolMetadata | None:
        return self._metadata.get(name)

    def all_metadata(self) -> dict[str, ToolMetadata]:
        return dict(self._metadata)

    def registered(self) -> dict[str, list[str]]:
        return {name: list(ops) for name, ops in self._operations.items()}

    def catalog(self) -> str:
        lines = ["Available tools:"]
        for name, ops in self._operations.items():
            lines.append(f"- {name}: {', '.join(ops)}")
        return "\n".join(lines)

    def tool_call_specs(self) -> tuple[object, ...]:
        """Provider-native function schemas for every registered tool.

        Returns infra ToolCallSpec objects (typed as object here to keep the
        registry free of provider-format imports at call sites).
        """
        from atlas.infra.types import ToolCallSpec

        specs = []
        for name, meta in self._metadata.items():
            ops = self._operations.get(name, ())
            props: dict[str, object] = {
                "operation": {"type": "string", "enum": list(ops)},
                "args": {"type": "object", "description": "operation arguments"},
            }
            specs.append(
                ToolCallSpec(
                    name=name,
                    description=meta.description or f"Execute a {name} operation.",
                    parameters={"type": "object", "properties": props, "required": ["operation"]},
                )
            )
        return tuple(specs)
