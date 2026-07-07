"""Tool registry — the tool-agnostic lookup seam.

WHY: the orchestrator gets tool NAMES from plans/actions and must resolve them
without importing concrete tools. The registry is the only thing that holds tool
instances; the dispatcher asks it by name. catalog() feeds the prompt so the
model knows what's available.
"""

from __future__ import annotations

from atlas.tools.base import Tool


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._operations: dict[str, tuple[str, ...]] = {}

    def register(self, tool: Tool, operations: tuple[str, ...]) -> None:
        self._tools[tool.name] = tool
        self._operations[tool.name] = operations

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def registered(self) -> dict[str, list[str]]:
        return {name: list(ops) for name, ops in self._operations.items()}

    def catalog(self) -> str:
        lines = ["Available tools:"]
        for name, ops in self._operations.items():
            lines.append(f"- {name}: {', '.join(ops)}")
        return "\n".join(lines)
