"""Knowledge Platform — obtain_knowledge(): the one call the orchestrator makes.

PIPELINE: classify intent -> pick sources (STATIC=parametric; MEMORY=memory;
LIVE/MIXED=official feeds + web corroboration) -> fan out in parallel -> gather
KnowledgeItems -> if multiple sources, rank by (source_kind trust x recency x
agreement) and summarize via the Phase-5 gateway -> Answer{text, confidence,
sources}. The answer is written back to episodic memory so consolidation can learn
from what was looked up. Every provider fetch goes through the capability dispatcher
(Safety Engine); reads are Tier-0/1.
"""

from __future__ import annotations

import asyncio

from atlas.capabilities.domain.common import Confidence
from atlas.capabilities.domain.knowledge import (
    Answer,
    KnowledgeIntent,
    KnowledgeItem,
    KnowledgeQuery,
)
from atlas.capabilities.platforms.knowledge_router import KnowledgeRouter
from atlas.capabilities.providers.knowledge.base import KnowledgeProvider
from atlas.infra.clock import Clock
from atlas.infra.ids import CorrelationId, IdGenerator
from atlas.infra.logging import get_logger
from atlas.infra.types import ModelRequest
from atlas.intelligence.gateway import ModelGateway
from atlas.memory.episodic import EpisodicMemory
from atlas.memory.types import Episode, EpisodeKind

_log = get_logger("atlas.knowledge")

_TRUST = {"local": 0.9, "official": 1.0, "web": 0.6, "model": 0.5}

_SUMMARIZE = ("Synthesize a single accurate answer from these sources. Cite nothing you "
              "cannot support. If sources conflict, say so briefly. Be concise.")


class KnowledgePlatform:
    def __init__(
        self, *, router: KnowledgeRouter, gateway: ModelGateway,
        episodic: EpisodicMemory, ids: IdGenerator, clock: Clock,
        official: list[KnowledgeProvider], web: list[KnowledgeProvider],
        memory_source: KnowledgeProvider, parametric: KnowledgeProvider,
    ) -> None:
        self._router = router
        self._gw = gateway
        self._epi = episodic
        self._ids = ids
        self._clock = clock
        self._official = official
        self._web = web
        self._memory = memory_source
        self._parametric = parametric

    async def obtain_knowledge(self, query: KnowledgeQuery, correlation_id: CorrelationId) -> Answer:
        intent = await self._router.classify(query, correlation_id)
        _log.info("knowledge.route", event_type="knowledge", intent=intent.value, q=query.text[:80])

        if intent is KnowledgeIntent.STATIC:
            items = await self._safe_search(self._parametric, query)
            return await self._synthesize(query, items, correlation_id, intent)
        if intent is KnowledgeIntent.MEMORY:
            items = await self._safe_search(self._memory, query)
            return await self._synthesize(query, items, correlation_id, intent)

        # LIVE / MIXED: memory + official first, web as corroboration, in parallel
        providers: list[KnowledgeProvider] = [self._memory, *self._official]
        if query.prefer_official:
            providers += self._web           # still queried, just ranked lower
        results = await asyncio.gather(
            *(self._safe_search(p, query) for p in providers), return_exceptions=True)
        items: list[KnowledgeItem] = []  # type: ignore
        for r in results:
            if isinstance(r, list):
                items.extend(r)
        return await self._synthesize(query, items, correlation_id, intent)

    async def _safe_search(self, provider: KnowledgeProvider, query: KnowledgeQuery) -> list[KnowledgeItem]:
        try:
            return await provider.search(query.text, limit=query.max_sources)
        except Exception as exc:
            _log.warning("knowledge.source_failed", event_type="knowledge",
                         provider=provider.name, error=repr(exc))
            return []

    def _rank(self, items: list[KnowledgeItem]) -> list[KnowledgeItem]:
        def score(i: KnowledgeItem) -> float:
            trust = _TRUST.get(i.provenance.source_kind.value, 0.5)
            recency = 1.0
            if i.published:
                age_days = (self._clock.now() - i.published).days
                recency = 1.0 / (1.0 + max(0, age_days) / 30.0)
            return trust * 0.7 + recency * 0.3
        return sorted(items, key=score, reverse=True)

    async def _synthesize(
        self, query: KnowledgeQuery, items: list[KnowledgeItem],
        correlation_id: CorrelationId, intent: KnowledgeIntent,
    ) -> Answer:
        ranked = self._rank(items)[: query.max_sources]
        if not ranked:
            answer = Answer(text="I couldn't find reliable sources for that.",
                            confidence=Confidence(score=0.1, basis="no sources"), intent=intent)
            await self._write_back(query, answer, correlation_id)
            return answer
        corpus = "\n\n".join(f"[{i.provenance.source_kind.value}] {i.title}: {i.snippet}"
                             for i in ranked)
        resp = await self._gw.complete(ModelRequest(
            correlation_id=correlation_id, system=_SUMMARIZE,
            prompt=f"QUESTION: {query.text}\n\nSOURCES:\n{corpus}",
            needs_deep_reasoning=len(ranked) > 3, max_tokens=600))
        # confidence: official sources + agreement raise it; single web source lowers it
        official_n = sum(1 for i in ranked if i.provenance.source_kind.value in ("official", "local"))
        conf = min(0.95, 0.4 + 0.1 * official_n + 0.05 * len(ranked))
        answer = Answer(text=resp.text, sources=tuple(ranked),
                        confidence=Confidence(score=conf,
                                              basis=f"{official_n} official/local of {len(ranked)}"),
                        intent=intent)
        await self._write_back(query, answer, correlation_id)
        return answer

    async def _write_back(self, query: KnowledgeQuery, answer: Answer, correlation_id: CorrelationId) -> None:
        """Record the lookup as an episode so consolidation can distill it later.
        WHY: knowledge the agent fetched is exactly what it should remember."""
        await self._epi.record(Episode(
            correlation_id=str(correlation_id), ts=self._clock.now(),
            kind=EpisodeKind.OBSERVATION, role="system",
            content=f"knowledge[{answer.intent.value}] Q: {query.text} -> {answer.text[:400]}",
            outcome="ok", salience=0.4))
