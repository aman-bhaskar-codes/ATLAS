"""Memory↔RAG fusion with SEPARATE provenance (§45-47).

Memory is a knowledge source, not a secret side-channel: facts/episodes surface
as Evidence with `source=MEMORY`, their own authority, and explicit provenance
so synthesis and citations never blur "what I remember" with "what the web
says". The existing `Retriever` does the actual recall — we only translate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from atlas.infra.clock import Clock
from atlas.knowledge.domain import Evidence, SourceType, make_evidence_id

if TYPE_CHECKING:
    pass


class FactLike(Protocol):
    text: str


class RetrieverLike(Protocol):
    async def retrieve(self, query: str, **kwargs: Any) -> Any: ...


class MemoryBridge:
    """Adapts the memory Retriever into fabric Evidence."""

    def __init__(self, retriever: RetrieverLike, clock: Clock, *, authority: float = 0.6) -> None:
        self._retriever = retriever
        self._clock = clock
        self._authority = authority

    async def evidence_for(self, query: str, *, limit: int = 3) -> list[Evidence]:
        ctx = await self._retriever.retrieve(query)
        out: list[Evidence] = []
        facts: list[FactLike] = list(getattr(ctx, "facts", []) or [])[:limit]
        for fact in facts:
            text = getattr(fact, "text", "")
            if not text:
                continue
            salience = float(getattr(fact, "salience", 0.5))
            ev = Evidence(
                evidence_id=make_evidence_id("memory://semantic", text),
                document_id="memory://semantic",
                chunk_id="memory",
                source=SourceType.MEMORY,
                quote=text,
                location="semantic memory",
                uri="memory://semantic",
                title="ATLAS semantic memory",
                retrieved_at=self._clock.now(),
                authority=self._authority,
                confidence=round(min(0.9, 0.4 + 0.5 * salience), 3),
                provenance={"layer": "semantic", "salience": salience},
            ).with_hash()
            out.append(ev)
        # Episodes add recency context when facts are thin.
        if len(out) < limit:
            episodes = list(getattr(ctx, "recent_episodes", []) or [])[: limit - len(out)]
            for ep in episodes:
                content = getattr(ep, "content", "")
                if not content:
                    continue
                ev = Evidence(
                    evidence_id=make_evidence_id("memory://episodic", content),
                    document_id="memory://episodic",
                    chunk_id="memory",
                    source=SourceType.EXPERIENCE,
                    quote=content[:400],
                    location="episodic memory",
                    uri="memory://episodic",
                    title="ATLAS episodic memory",
                    retrieved_at=self._clock.now(),
                    authority=self._authority - 0.1,
                    confidence=0.5,
                    provenance={"layer": "episodic"},
                ).with_hash()
                out.append(ev)
        return out
