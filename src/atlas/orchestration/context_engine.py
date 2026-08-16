"""Context engineering — budget, rank, compact.

WHY: the reasoning loop's history grows every step; without compaction the
prompt eventually crowds out the goal. These three pieces make context a
managed resource: a budget (token ceiling), a ranker (what matters), and a
compactor (collapse old turns into a summary line). All pure/synchronous —
no I/O, no model calls; the LLM-free summarizer trades nuance for determinism
and zero latency.
"""

from __future__ import annotations

from dataclasses import dataclass

from atlas.orchestration.types import Observation, Thought


def estimate_tokens(text: str) -> int:
    """Coarse token estimate (~4 chars/token). Good enough for budgeting."""
    return max(1, len(text) // 4)


@dataclass(frozen=True)
class ContextBudget:
    """Token ceilings for one reasoning prompt."""

    total: int = 6000
    max_history_turns: int = 8

    @property
    def history(self) -> int:
        return max(500, self.total - 2000)  # reserve goal/context room


class ContextRanker:
    """Scores history turns by recency + failure salience.

    Recent turns matter most (recency decay); failures stay visible longer
    than successes (the loop must not repeat a mistake it just made).
    """

    @staticmethod
    def score(index: int, total: int, observation: Observation | None) -> float:
        recency = (index + 1) / max(1, total)
        if observation is None:
            return recency
        failure_boost = 0.35 if not observation.ok else 0.0
        return min(1.0, recency + failure_boost)


class ContextCompactor:
    """Collapses low-ranked old turns into one summary line."""

    def __init__(self, budget: ContextBudget | None = None) -> None:
        self.budget = budget or ContextBudget()

    def compact(
        self,
        history: list[tuple[Thought, Observation | None]],
    ) -> list[tuple[Thought, Observation | None]]:
        """Return history within turn + token budgets, oldest overflow summarized.

        Deterministic: keep the newest N turns that fit the token budget;
        everything older collapses into a single synthetic Thought placed at
        the head. Never returns more than max_history_turns + 1 entries.
        """
        if len(history) <= self.budget.max_history_turns:
            return history

        total = len(history)
        # Newest turns first until the token budget is spent.
        kept: list[tuple[Thought, Observation | None]] = []
        used = 0
        for thought, obs in reversed(history):
            cost = estimate_tokens(thought.content) + (
                estimate_tokens(str(obs.content)[:500]) if obs and obs.content else 20
            )
            if len(kept) >= self.budget.max_history_turns or used + cost > self.budget.history:
                break
            kept.append((thought, obs))
            used += cost
        kept.reverse()

        if not kept:
            kept = [history[-1]]  # always keep at least the latest turn

        dropped = total - len(kept)
        if dropped <= 0:
            return kept

        failures = sum(1 for _, obs in history[: total - len(kept)] if obs is not None and not obs.ok)
        summary = Thought(
            step=0,
            content=(
                f"[compact] {dropped} earlier steps summarized: "
                f"{failures} failure(s), {dropped - failures} success(es). "
                "Key context is preserved below."
            ),
            confidence=0.5,
        )
        return [(_summary_thought(summary), None), *kept]

    @staticmethod
    def render(history: list[tuple[Thought, Observation | None]]) -> str:
        """Render compacted history as prompt text."""
        lines = []
        for thought, obs in history:
            if obs is None:
                lines.append(f"T: {thought.content[:200]}")
            else:
                status = "ok" if obs.ok else f"FAILED ({(obs.error or '')[:120]})"
                content = str(obs.content)[:300] if obs.content else ""
                lines.append(f"T: {thought.content[:120]}\nO: [{status}] {content}")
        return "\n".join(lines)


def _summary_thought(t: Thought) -> Thought:
    return t
