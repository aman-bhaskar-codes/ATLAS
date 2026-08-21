"""KnowledgeFabric — the ONE entry point for knowledge (§0, §2, §55).

query() walks the canonical pipeline: ROUTE → RETRIEVE (hybrid) → RERANK →
EVIDENCE → CONTRADICTIONS → SYNTHESIZE → VERIFY CLAIMS → CITE → ANSWER,
recording telemetry with machine-readable failure causes the whole way.

The fabric never blocks on evaluation or training (§130): those layers read
the telemetry/feedback stores offline.
"""

from __future__ import annotations

import time
from typing import Any, Protocol

from atlas.infra.clock import Clock
from atlas.infra.ids import IdGenerator
from atlas.infra.logging import get_logger
from atlas.knowledge.cache import QueryResultCache
from atlas.knowledge.domain import Evidence, FailureCause, QueryRoute, RAGMode
from atlas.knowledge.evidence import ClaimExtractor, ClaimVerifier, ContradictionDetector, EvidenceSelector
from atlas.knowledge.reranking import FeatureReranker
from atlas.knowledge.retrieval import HybridRetriever
from atlas.knowledge.router import QueryRouter
from atlas.knowledge.synthesis import AnswerSynthesizer, FabricAnswer
from atlas.knowledge.telemetry import RagRecord, RagTelemetry

_log = get_logger("atlas.knowledge.fabric")

_ROUTE_TO_MODE: dict[QueryRoute, RAGMode] = {
    QueryRoute.COMPUTATIONAL: RAGMode.DIRECT,
    QueryRoute.STATIC_KNOWLEDGE: RAGMode.DIRECT,
    QueryRoute.PRIVATE_KNOWLEDGE: RAGMode.MEMORY_RAG,
    QueryRoute.MEMORY: RAGMode.MEMORY_RAG,
    QueryRoute.CODEBASE: RAGMode.CODEBASE_RAG,
    QueryRoute.RESEARCH: RAGMode.DEEP_RESEARCH,
    QueryRoute.MULTI_HOP: RAGMode.MULTI_HOP_RAG,
    QueryRoute.LIVE: RAGMode.HYBRID,
    QueryRoute.MIXED: RAGMode.RAG,
}

_MODE_SOURCE_FILTER: dict[RAGMode, tuple[str, ...] | None] = {
    RAGMode.MEMORY_RAG: ("memory", "experience", "local_file", "user_provided"),
    RAGMode.CODEBASE_RAG: ("local_file",),
    RAGMode.BROWSER_RAG: ("browser_page", "web_page"),
    RAGMode.RAG: None,
    RAGMode.HYBRID: None,
    RAGMode.DEEP_RESEARCH: None,
    RAGMode.MULTI_HOP_RAG: None,
    RAGMode.RESEARCH_GRAPH: None,
    RAGMode.DIRECT: (),
}


class MemorySource(Protocol):
    """Anything that can surface memory as Evidence (see memory_fusion.py)."""

    async def evidence_for(self, query: str, *, limit: int) -> list[Evidence]: ...


class KnowledgeFabric:
    def __init__(
        self,
        *,
        retriever: HybridRetriever,
        reranker: FeatureReranker,
        selector: EvidenceSelector,
        contradictions: ContradictionDetector,
        claims: ClaimExtractor,
        verifier: ClaimVerifier,
        synthesizer: AnswerSynthesizer,
        router: QueryRouter,
        telemetry: RagTelemetry,
        ids: IdGenerator,
        clock: Clock,
        cache: QueryResultCache | None = None,
        memory: MemorySource | None = None,
    ) -> None:
        self._retriever = retriever
        self._reranker = reranker
        self._selector = selector
        self._contra = contradictions
        self._claims = claims
        self._verifier = verifier
        self._synth = synthesizer
        self._router = router
        self._telemetry = telemetry
        self._ids = ids
        self._clock = clock
        self._cache = cache or QueryResultCache()
        self._memory = memory

    # ── the canonical query path ─────────────────────────────────────
    async def query(
        self, text: str, *, mode: RAGMode | None = None, source_types: tuple[str, ...] | None = None
    ) -> FabricAnswer:
        t0 = time.monotonic()
        plan = self._router.route(text)
        resolved_mode = mode or _ROUTE_TO_MODE.get(plan.route, RAGMode.RAG)

        cache_key = self._cache.make_key(text, resolved_mode.value)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        filter_types = source_types or _MODE_SOURCE_FILTER.get(resolved_mode)
        failure: FailureCause | None = None
        retrieve_ms = rerank_ms = synthesize_ms = 0

        # ── retrieve (bounded multi-variant; multi-hop adds sub-questions) ──
        variants = list(plan.rewrites) or [text]
        if plan.sub_questions:
            variants = (variants + list(plan.sub_questions))[:4]
        rt = time.monotonic()
        retrieval = await self._retriever.retrieve_all(variants, k=50, source_types=filter_types)
        retrieve_ms = int((time.monotonic() - rt) * 1000)

        # ── rerank → evidence (a broken reranker falls back to retrieval order) ──
        rt = time.monotonic()
        try:
            reranked = self._reranker.rerank(text, retrieval.candidates, k=20)
        except Exception as exc:
            reranked = retrieval.candidates[:20]
            _log.warning("fabric.rerank_failed", event_type="knowledge", error=repr(exc))
        rerank_ms = int((time.monotonic() - rt) * 1000)
        evidence = self._selector.select(text, reranked[:10])

        # ── memory fusion with separate provenance (§45-47) ──────────
        if self._memory is not None and resolved_mode in (
            RAGMode.MEMORY_RAG,
            RAGMode.HYBRID,
            RAGMode.RAG,
            RAGMode.MULTI_HOP_RAG,
        ):
            try:
                memory_ev = await self._memory.evidence_for(text, limit=3)
                evidence = _merge_evidence(evidence, memory_ev)
            except Exception as exc:
                _log.warning("fabric.memory_fusion_failed", event_type="knowledge", error=repr(exc))

        contradictions = self._contra.detect(text, evidence)

        if not retrieval.candidates and resolved_mode not in (RAGMode.DIRECT,):
            failure = FailureCause.RETRIEVAL_MISS
        elif retrieval.degraded and not retrieval.candidates:
            failure = FailureCause.RETRIEVAL_MISS

        # ── synthesize (evidence-first) ──────────────────────────────
        rt = time.monotonic()
        answer = await self._synth.synthesize(
            text,
            plan,
            evidence,
            contradictions,
            claims=[],  # extracted after synthesis from the answer text
            mode=resolved_mode,
            degraded=retrieval.degraded,
            degradation_reason=retrieval.degradation_reason,
        )
        synthesize_ms = int((time.monotonic() - rt) * 1000)

        # ── ground-check claims made in the answer (§124) ────────────
        if answer.answered:
            raw_claims = self._claims.extract(answer.text)
            verified = self._verifier.verify(raw_claims, evidence, contradictions)
            answer = _with_claims(answer, verified)

        if answer.answered and not evidence:
            failure = FailureCause.ANSWER_GROUNDING_FAILURE
        if contradictions and failure is None and answer.answered:
            # conflicts are surfaced, not fatal — record for the eval layer
            answer.detail["contradiction_count"] = len(contradictions)

        # ── telemetry (§98) + cache ──────────────────────────────────
        record = RagRecord(
            record_id=f"rag_{self._ids.execution_id()}",
            query=text,
            mode=resolved_mode,
            route=plan.route,
            created_ts=self._clock.now(),
            latency_ms=int((time.monotonic() - t0) * 1000),
            retrieve_ms=retrieve_ms,
            rerank_ms=rerank_ms,
            synthesize_ms=synthesize_ms,
            candidate_count=len(retrieval.candidates),
            evidence_count=len(evidence),
            answered=answer.answered,
            degraded=retrieval.degraded,
            failure=failure,
            detail={"signals": list(plan.signals), **answer.detail},
        )
        self._telemetry.record(record)
        await self._telemetry.persist(record)
        self._cache.put(cache_key, answer)
        return answer

    def invalidate_cache(self) -> None:
        self._cache.invalidate()


def _merge_evidence(primary: list[Evidence], extra: list[Evidence], *, cap: int = 12) -> list[Evidence]:
    seen = {e.hash for e in primary if e.hash}
    merged = list(primary)
    for ev in extra:
        if ev.hash and ev.hash in seen:
            continue
        merged.append(ev)
        if len(merged) >= cap:
            break
    return merged


def _with_claims(answer: FabricAnswer, claims: Any) -> FabricAnswer:
    from dataclasses import replace

    return replace(answer, claims=tuple(claims))
