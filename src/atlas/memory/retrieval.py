"""Hybrid retrieval — the read path before every decision.

WHY RRF (Reciprocal Rank Fusion): combining a dense ranking and a sparse ranking
by score is fragile (different scales); RRF combines by RANK, is parameter-light,
and just works. WHY a token budget: context is finite and expensive; we pack the
highest fused-score items until the budget is spent, never 'everything'.

Phase 3: Enhanced with vector-based semantic search for facts, episodes, and knowledge store.
Phase 3.8: RetrievalCache for sub-1ms repeated queries; invalidation on any memory write.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from atlas.infra.logging import get_logger
from atlas.memory.cache import RetrievalCache
from atlas.memory.episodic import EpisodicMemory
from atlas.memory.semantic import SemanticMemory
from atlas.memory.types import Episode, RetrievedContext, SemanticFact
from atlas.memory.user_model import UserModel

if TYPE_CHECKING:
    from atlas.memory.knowledge_store import KnowledgeStore

_log = get_logger("atlas.memory.retrieval")
_RRF_K = 60  # standard RRF constant


class Retriever:
    def __init__(
        self,
        *,
        semantic: SemanticMemory,
        episodic: EpisodicMemory,
        user_model: UserModel,
        knowledge_store: KnowledgeStore | None = None,
        token_budget: int = 1500,
        events: Any | None = None,  # EventPublisher - Any to avoid circular import
        cache_ttl: float = 30.0,  # seconds; 0 disables caching
    ) -> None:
        self._sem = semantic
        self._epi = episodic
        self._um = user_model
        self._knowledge = knowledge_store
        self._budget = token_budget
        self._events = events
        self._cache: RetrievalCache | None = RetrievalCache(ttl=cache_ttl) if cache_ttl > 0 else None

    def set_events(self, events: Any) -> None:
        """Set EventPublisher after construction."""
        self._events = events

    def set_knowledge_store(self, knowledge_store: KnowledgeStore) -> None:
        """Set KnowledgeStore after construction (avoids circular import)."""
        self._knowledge = knowledge_store

    async def invalidate_cache(self) -> None:
        """Flush the retrieval cache — call after any memory write."""
        if self._cache:
            await self._cache.invalidate()

    async def retrieve(
        self,
        query: str,
        *,
        terms: list[str] | None = None,
        task_id: str | None = None,
        correlation_id: str | None = None,
    ) -> RetrievedContext:
        """
        Hybrid retrieval with semantic search over facts, episodes, and knowledge store.

        Performance targets:
          CACHE HIT:  < 1 ms   (in-memory dict lookup)
          CACHE MISS: < 200 ms (5 parallel async queries + RRF fusion)
        Token budget: 1500 tokens total, max 500 for knowledge chunks.
        """
        import asyncio

        # ── Cache check ──────────────────────────────────────────────────
        if self._cache:
            cache_key = self._cache.make_key(query, task_id)
            cached = await self._cache.get(cache_key)
            if cached is not None and isinstance(cached, RetrievedContext):
                _log.debug("retrieval.cache_hit", event_type="memory", query=query[:50])
                return cached

        t0 = time.monotonic()
        dense_task = asyncio.create_task(self._sem.semantic_search(query, k=15))
        sparse_task = asyncio.create_task(self._epi.keyword_search(terms or query.split(), limit=15))
        semantic_episodes_task = asyncio.create_task(self._epi.semantic_search(query, limit=10, min_salience=0.3))
        user_model_task = asyncio.create_task(self._um.render())

        # Phase 3: Query knowledge store if available
        if self._knowledge:
            knowledge_task = asyncio.create_task(self._knowledge.search(query, limit=5))
            dense, sparse, semantic_episodes, user_model, knowledge_results = await asyncio.gather(
                dense_task, sparse_task, semantic_episodes_task, user_model_task, knowledge_task
            )
        else:
            dense, sparse, semantic_episodes, user_model = await asyncio.gather(
                dense_task, sparse_task, semantic_episodes_task, user_model_task
            )
            knowledge_results = []

        # 3. fuse facts by RRF rank (dense list) + salience boost
        ranked_facts = self._rrf_facts(dense)

        # 4. fuse episodes: semantic (vector) + sparse (keyword)
        ranked_episodes = self._fuse_episodes(semantic_episodes, sparse)

        # 5. knapsack into the token budget (facts first, episodes second, knowledge third)
        # Reserve up to 500 tokens for knowledge chunks
        knowledge_budget = min(500, self._budget // 3)
        memory_budget = self._budget - knowledge_budget

        facts, epis, used = self._pack(ranked_facts, ranked_episodes, budget=memory_budget)

        # 6. pack knowledge chunks within their budget
        knowledge_chunks, knowledge_tokens = self._pack_knowledge(knowledge_results, budget=knowledge_budget)
        used += knowledge_tokens

        # Emit memory retrieval event
        if self._events and task_id and correlation_id:
            try:
                await self._events.emit_memory(
                    task_id=task_id,
                    correlation_id=correlation_id,
                    kind="memory.retrieved",
                    memory_type="hybrid",
                    count=len(facts) + len(epis) + len(knowledge_chunks),
                    query=query[:100],
                    items=[f.text[:50] for f in facts[:5]],  # Sample of retrieved facts
                )
            except Exception as exc:
                _log.warning("retrieval.emit_error", event_type="memory", error=str(exc))

        _log.debug(
            "retrieval.complete",
            event_type="memory",
            facts_count=len(facts),
            episodes_count=len(epis),
            knowledge_count=len(knowledge_chunks),
            tokens_used=used,
            query=query[:50],
        )

        result = RetrievedContext(
            user_model=user_model,
            facts=tuple(facts),
            recent_episodes=tuple(epis),
            knowledge_chunks=tuple(knowledge_chunks),
            token_estimate=used,
        )

        latency_ms = int((time.monotonic() - t0) * 1000)
        _log.debug(
            "retrieval.complete",
            event_type="memory",
            facts_count=len(facts),
            episodes_count=len(epis),
            knowledge_count=len(knowledge_chunks),
            tokens_used=used,
            latency_ms=latency_ms,
            query=query[:50],
        )

        # ── Cache store ─────────────────────────────────────────────────
        if self._cache and cache_key:
            await self._cache.set(cache_key, result)

        return result

    def _rrf_facts(self, dense: list[SemanticFact]) -> list[SemanticFact]:
        """Rank facts by RRF + salience boost."""
        scored: list[tuple[float, SemanticFact]] = []
        for rank, f in enumerate(dense):
            rrf = 1.0 / (_RRF_K + rank)
            scored.append((rrf + 0.1 * f.salience, f))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [f for _, f in scored]

    def _fuse_episodes(self, semantic: list[Episode], sparse: list[Episode]) -> list[Episode]:
        """Fuse semantic (vector) and sparse (keyword) episode rankings using RRF."""
        # Build rank maps
        semantic_ranks = {ep.id: i for i, ep in enumerate(semantic) if ep.id}
        sparse_ranks = {ep.id: i for i, ep in enumerate(sparse) if ep.id}

        # Collect all unique episodes
        all_episodes = {ep.id: ep for ep in semantic + sparse if ep.id}

        # Score by RRF
        scored: list[tuple[float, Episode]] = []
        for ep_id, ep in all_episodes.items():
            rrf_score = 0.0

            if ep_id in semantic_ranks:
                rrf_score += 1.0 / (_RRF_K + semantic_ranks[ep_id])

            if ep_id in sparse_ranks:
                rrf_score += 0.5 / (_RRF_K + sparse_ranks[ep_id])  # Weight sparse lower

            # Boost by salience
            rrf_score += 0.1 * ep.salience

            scored.append((rrf_score, ep))

        scored.sort(key=lambda t: t[0], reverse=True)
        return [ep for _, ep in scored]

    @staticmethod
    def _tokens(text: str) -> int:
        return max(1, len(text) // 4)  # coarse estimate; good enough for budgeting

    def _pack(
        self,
        facts: list[SemanticFact],
        epis: list[Episode],
        *,
        budget: int,
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

    def _pack_knowledge(
        self, knowledge_results: list[dict[str, Any]], *, budget: int
    ) -> tuple[list[dict[str, Any]], int]:
        """Pack knowledge chunks within token budget."""
        used = 0
        chosen: list[dict[str, Any]] = []

        for chunk in knowledge_results:
            content = chunk.get("content", "")
            cost = self._tokens(content)
            if used + cost > budget:
                break
            chosen.append(chunk)
            used += cost

        return chosen, used
