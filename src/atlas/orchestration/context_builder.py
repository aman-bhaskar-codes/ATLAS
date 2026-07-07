"""Context builder — deterministic, layered, token-budgeted.

WHY fixed ordering + budget: the planner/reasoner must see context in a stable
order (so behavior is reproducible) and never overflow the window. Highest-
authority, cheapest-to-keep layers come first (system, safety, user-model); the
most negotiable (extra episodes) are trimmed first when the budget is tight.
Retrieval itself is already budgeted (Phase 3); this composes the rest around it.
"""

from __future__ import annotations

from dataclasses import dataclass

from atlas.memory.retrieval import Retriever
from atlas.memory.working import WorkingMemory


@dataclass(frozen=True)
class ContextLayer:
    name: str
    body: str
    priority: int  # lower = kept first when trimming


class ContextBuilder:
    def __init__(
        self, *, retriever: Retriever, working: WorkingMemory,
        system_prompt: str, token_budget: int = 3000,
    ) -> None:
        self._retriever = retriever
        self._working = working
        self._system = system_prompt
        self._budget = token_budget

    @staticmethod
    def _tokens(text: str) -> int:
        return max(1, len(text) // 4)

    async def build(
        self, request: str, *, safety_constraints: str, tool_catalog: str,
        plan_summary: str | None = None,
    ) -> str:
        retrieved = await self._retriever.retrieve(request)
        working = "\n".join(e.content[:200] for e in self._working.recent(10))

        layers: list[ContextLayer] = [
            ContextLayer("system", self._system, 0),
            ContextLayer("safety", safety_constraints, 1),
            ContextLayer("user_model", retrieved.user_model, 2),
            ContextLayer("tools", tool_catalog, 3),
            ContextLayer("memory", self._render_memory(retrieved), 4),
            ContextLayer("working", working, 5),
        ]
        if plan_summary:
            layers.append(ContextLayer("plan", plan_summary, 3))

        # deterministic order = priority, then declaration order
        layers.sort(key=lambda ly: ly.priority)
        chosen: list[str] = []
        used = 0
        for ly in layers:
            if not ly.body.strip():
                continue
            block = f"### {ly.name}\n{ly.body}"
            cost = self._tokens(block)
            if used + cost > self._budget and ly.priority > 2:
                continue  # trim only negotiable layers
            chosen.append(block)
            used += cost
        return "\n\n".join(chosen)

    @staticmethod
    def _render_memory(retrieved: object) -> str:
        facts = getattr(retrieved, "facts", ())
        return "\n".join(f"- {f.text}" for f in facts)
