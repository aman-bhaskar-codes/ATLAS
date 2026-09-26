"""Tool metadata, health tracking, and intelligent routing.

WHY: exposing every tool to every model call wastes context and invites bad
choices. Metadata (cost/latency/side-effects/idempotency) lets the planner and
router make informed decisions; live health scores deprioritize tools that are
currently failing or slow. Ranking is a pure function of metadata + health +
lexical relevance to the task — no LLM call, no embedding call, sub-millisecond.

M0.4 hardening: `rank` now honors `intent` via lexical Tool-RAG (keyword
affinity to a tool's name/operations/description), and `shortlist_specs` returns
the bounded, relevance-ranked ToolCallSpec set the agent engine (M0.2) advertises
to the model — so the model sees a small relevant toolset, not everything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from atlas.infra.types import ToolCallSpec
from atlas.orchestration.registry import ToolMetadata, ToolRegistry

_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


@dataclass
class ToolHealthTracker:
    """Live per-tool success/latency tracking (EWMA).

    Single-user scale: in-memory is correct and sufficient; the interface is
    the seam a durable implementation can replace later. EWMA decay (alpha
    0.3) makes recent behavior dominate — one old failure won't pin a tool.
    """

    alpha: float = 0.3
    _stats: dict[str, dict[str, float]] = field(default_factory=dict)

    def record(self, tool: str, *, ok: bool, latency_ms: int) -> None:
        s = self._stats.setdefault(
            tool,
            {
                "success_ewma": 1.0,
                "latency_ewma": 100.0,
                "calls": 0.0,
                "failures": 0.0,
            },
        )
        outcome = 1.0 if ok else 0.0
        s["success_ewma"] = self.alpha * outcome + (1 - self.alpha) * s["success_ewma"]
        s["latency_ewma"] = self.alpha * float(latency_ms) + (1 - self.alpha) * s["latency_ewma"]
        s["calls"] += 1
        if not ok:
            s["failures"] += 1

    def health(self, tool: str) -> float:
        """0.0-1.0. Unknown tools default to 0.5 (neutral, not optimistic)."""
        s = self._stats.get(tool)
        if s is None or s["calls"] == 0:
            return 0.5
        return max(0.0, min(1.0, s["success_ewma"]))

    def latency_ms(self, tool: str) -> float:
        s = self._stats.get(tool)
        return s["latency_ewma"] if s else 500.0

    def snapshot(self) -> dict[str, dict[str, float]]:
        return {k: dict(v) for k, v in self._stats.items()}


class ToolRouter:
    """Ranks registered tools for a task context.

    Score = health (0.5) + idempotency bonus (0.2) + cheapness (0.2) +
    speed (0.1) + lexical relevance to the intent (up to 0.6 when an intent is
    given), with side-effecting tools penalized unless required. Safety tiering
    is NOT done here — the Safety Engine remains the sole enforcement layer;
    this ranking only orders candidates before dispatch.
    """

    _AFFINITY_WEIGHT = 0.6  # how hard a keyword match pulls a tool up the ranking

    def __init__(self, registry: ToolRegistry, health: ToolHealthTracker) -> None:
        self._registry = registry
        self._health = health

    def rank(self, intent: str = "", *, needs_side_effects: bool = False) -> list[str]:
        intent_tokens = _tokens(intent)
        scored: list[tuple[float, str]] = []
        for name, meta in self._registry.all_metadata().items():
            health = self._health.health(name)
            latency = self._health.latency_ms(name)
            meta_latency = meta.estimated_latency_ms if meta else 500
            expected_latency = max(latency, float(meta_latency) * 0.5) if meta else latency

            score = 0.5 * health
            if meta is not None:
                if meta.idempotent:
                    score += 0.2
                if meta.estimated_cost_usd == 0.0:
                    score += 0.2
                elif meta.estimated_cost_usd < 0.01:
                    score += 0.1
                if expected_latency < 500:
                    score += 0.1
                elif expected_latency < 2000:
                    score += 0.05
                if meta.side_effects and not needs_side_effects:
                    score -= 0.15
            if intent_tokens:
                score += self._AFFINITY_WEIGHT * self._affinity(intent_tokens, name, meta)
            scored.append((score, name))
        scored.sort(reverse=True)
        return [name for _, name in scored]

    @staticmethod
    def _affinity(intent_tokens: set[str], name: str, meta: ToolMetadata | None) -> float:
        """Lexical relevance in [0, 1]: how well the intent's words hit this tool.

        Name and operation matches are strong signals (the model asked for what
        the tool literally does); description matches count half. Normalized by
        the query length so a short, on-target intent can still score high.
        """
        strong = _tokens(name)
        desc_tokens: set[str] = set()
        if meta is not None:
            for op in meta.operations:
                strong |= _tokens(op)
            desc_tokens = _tokens(meta.description)
        strong_hits = len(intent_tokens & strong)
        desc_hits = len(intent_tokens & (desc_tokens - strong))
        raw = strong_hits + 0.5 * desc_hits
        return min(1.0, raw / max(1, len(intent_tokens)))

    def shortlist_specs(
        self, intent: str = "", *, max_tools: int = 8, needs_side_effects: bool = False
    ) -> tuple[ToolCallSpec, ...]:
        """The bounded, relevance-ranked ToolCallSpec set for the agent engine.

        Ranks with `rank(intent)`, keeps the top `max_tools`, and returns their
        provider-native specs in rank order. This is the Tool-RAG shortlist the
        model is shown — a small relevant toolset instead of every registered
        tool. MCP-discovered specs can be merged by the caller before dispatch.
        """
        ranked = self.rank(intent, needs_side_effects=needs_side_effects)[:max_tools]
        specs_by_name: dict[str, ToolCallSpec] = {
            spec.name: spec for spec in self._registry.tool_call_specs() if isinstance(spec, ToolCallSpec)
        }
        return tuple(specs_by_name[name] for name in ranked if name in specs_by_name)

    def catalog(self, max_tools: int = 8) -> str:
        """Richer catalog for prompts: metadata hints per tool."""
        lines = ["Available tools (ranked by health/cost):"]
        for name in self.rank()[:max_tools]:
            meta = self._registry.metadata(name)
            if meta is None:
                lines.append(f"- {name}")
                continue
            flags = []
            if meta.idempotent:
                flags.append("idempotent")
            if meta.side_effects:
                flags.append("side-effects")
            if meta.supports_rollback:
                flags.append("rollback")
            hint = f" ({', '.join(flags)})" if flags else ""
            lines.append(f"- {name}: {', '.join(meta.operations)}{hint} — {meta.description}")
        return "\n".join(lines)
