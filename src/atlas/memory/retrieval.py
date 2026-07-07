"""Hybrid retrieval — the read path before every decision.

WHY RRF (Reciprocal Rank Fusion): combining a dense ranking and a sparse ranking
by score is fragile (different scales); RRF combines by RANK, is parameter-light,
and just works. WHY a token budget: context is finite and expensive; we pack the
highest fused-score items until the budget is spent, never 'everything'.
"""

from __future__ import annotations

from atlas.memory.episodic import EpisodicMemory
from atlas.memory.semantic import SemanticMemory
from atlas.memory.types import Episode, RetrievedContext, SemanticFact
from atlas.memory.user_model import UserModel

_RRF_K = 60  # standard RRF constant


class Retriever:
    def __init__(
        self, *, semantic: SemanticMemory, episodic: EpisodicMemory,
        user_model: UserModel, token_budget: int = 1500,
    ) -> None:
        self._sem = semantic
        self._epi = episodic
        self._um = user_model
        self._budget = token_budget

    async def retrieve(self, query: str, *, terms: list[str] | None = None) -> RetrievedContext:
        # 1. dense (meaning) over semantic facts
        dense = await self._sem.semantic_search(query, k=15)
        # 2. sparse (exact) over recent episodic
        sparse = await self._epi.keyword_search(terms or query.split(), limit=15)

        # 3. fuse facts by RRF rank (dense list) + salience boost
        ranked_facts = self._rrf_facts(dense)

        # 4. always-on user-model block
        user_model = await self._um.render()

        # 5. knapsack into the token budget (facts first, then recent episodes)
        facts, epis, used = self._pack(ranked_facts, sparse, budget=self._budget)
        return RetrievedContext(
            user_model=user_model, facts=tuple(facts),
            recent_episodes=tuple(epis), token_estimate=used,
        )

    def _rrf_facts(self, dense: list[SemanticFact]) -> list[SemanticFact]:
        # single dense ranking here; when KG (Phase 8.5) adds a third source,
        # fuse all rankings the same way. Salience nudges ties.
        scored: list[tuple[float, SemanticFact]] = []
        for rank, f in enumerate(dense):
            rrf = 1.0 / (_RRF_K + rank)
            scored.append((rrf + 0.1 * f.salience, f))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [f for _, f in scored]

    @staticmethod
    def _tokens(text: str) -> int:
        return max(1, len(text) // 4)  # coarse estimate; good enough for budgeting

    def _pack(
        self, facts: list[SemanticFact], epis: list[Episode], *, budget: int,
    ) -> tuple[list[SemanticFact], list[Episode], int]:
        used = 0
        chosen_f: list[SemanticFact] = []
        for f in facts:
            cost = self._tokens(f.text)
            if used + cost > budget:
                break
            chosen_f.append(f)
            used += cost
        chosen_e: list[Episode] = []
        for e in epis:
            cost = self._tokens(e.content)
            if used + cost > budget:
                break
            chosen_e.append(e)
            used += cost
        return chosen_f, chosen_e, used
