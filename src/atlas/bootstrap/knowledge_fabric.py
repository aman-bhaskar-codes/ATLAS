"""Knowledge Fabric bootstrap — wires the ONE pipeline into the runtime (§2, §131).

Reuses existing components instead of rebuilding them: SQLite (fabric_*
tables), ChromaVectorStore (dense leg), OllamaEmbedder, the memory Retriever
(memory fusion), and the KnowledgeProviders (live sources). The legacy
KnowledgePlatform remains the fast path; the fabric is the deep path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from atlas.infra.clock import Clock
from atlas.infra.db import Database
from atlas.infra.ids import IdGenerator
from atlas.infra.logging import get_logger
from atlas.infra.types import ModelCapability, ModelRequest
from atlas.knowledge.bm25 import BM25Index
from atlas.knowledge.browser_bridge import BrowserBridge
from atlas.knowledge.cache import QueryResultCache
from atlas.knowledge.citations import CitationEngine
from atlas.knowledge.codebase import CodebaseKnowledge
from atlas.knowledge.compression import CitationPreservingCompressor
from atlas.knowledge.deletion import ResearchMemory
from atlas.knowledge.engine import KnowledgeFabric
from atlas.knowledge.evidence import ClaimExtractor, ClaimVerifier, ContradictionDetector, EvidenceSelector
from atlas.knowledge.ingestion import IngestionPipeline
from atlas.knowledge.memory_fusion import MemoryBridge
from atlas.knowledge.providers_bridge import LiveBridge
from atlas.knowledge.reranking import FeatureReranker
from atlas.knowledge.research import ResearchRunner
from atlas.knowledge.retrieval import HybridRetriever
from atlas.knowledge.router import QueryRouter
from atlas.knowledge.store import FabricStore
from atlas.knowledge.synthesis import AnswerSynthesizer
from atlas.knowledge.telemetry import RagTelemetry

_log = get_logger("atlas.knowledge.fabric.bootstrap")


class GatewaySynthesizer:
    """Adapts ModelGateway.complete to the fabric's SynthesizerModel protocol."""

    def __init__(self, gateway: Any, ids: IdGenerator) -> None:
        self._gw = gateway
        self._ids = ids

    async def complete(self, system: str, prompt: str) -> str:
        resp = await self._gw.complete(
            ModelRequest(
                correlation_id=self._ids.correlation_id(),
                system=system,
                prompt=prompt,
                required_capabilities=frozenset({ModelCapability.SUMMARIZATION}),
                max_tokens=900,
            )
        )
        text: str = resp.text
        return text


@dataclass
class KnowledgeFabricComponents:
    fabric: KnowledgeFabric
    pipeline: IngestionPipeline
    store: FabricStore
    retriever: HybridRetriever
    research: ResearchRunner
    research_memory: ResearchMemory
    browser_bridge: BrowserBridge
    live_bridge: LiveBridge
    codebase: CodebaseKnowledge
    compressor: CitationPreservingCompressor
    telemetry: RagTelemetry


async def build_knowledge_fabric(
    *,
    db: Database,
    ids: IdGenerator,
    clock: Clock,
    gateway: Any,
    embedder: Any,
    vectors: Any,
    memory_retriever: Any,
    providers: list[Any] | None = None,
    use_models: bool = True,
) -> KnowledgeFabricComponents:
    store = FabricStore(db)
    hybrid = HybridRetriever(store, BM25Index(), embedder, vectors)
    # NOTE: BM25 corpus rebuild is LAZY (first retrieve) — the DB connection is
    # owned by the lifecycle and may not be up when bootstrap runs.

    pipeline = IngestionPipeline(store, hybrid, ids, clock, embedder=embedder, vector=vectors)
    telemetry = RagTelemetry()
    synthesizer = AnswerSynthesizer(CitationEngine(), model=GatewaySynthesizer(gateway, ids) if use_models else None)
    # The live bridge is the research runner's DISCOVERY leg. Without it the
    # runner can only re-read an already-warm index, so a genuinely new
    # question returns nothing.
    live_bridge = LiveBridge(providers or [], pipeline)
    fabric = KnowledgeFabric(
        retriever=hybrid,
        reranker=FeatureReranker(),
        selector=EvidenceSelector(ids, clock),
        contradictions=ContradictionDetector(),
        claims=ClaimExtractor(),
        verifier=ClaimVerifier(),
        synthesizer=synthesizer,
        router=QueryRouter(),
        telemetry=telemetry,
        ids=ids,
        clock=clock,
        cache=QueryResultCache(),
        memory=MemoryBridge(memory_retriever, clock),
    )
    components = KnowledgeFabricComponents(
        fabric=fabric,
        pipeline=pipeline,
        store=store,
        retriever=hybrid,
        research=ResearchRunner(hybrid, store, ids, clock, discovery=live_bridge if providers else None),
        research_memory=ResearchMemory(store, hybrid, vectors),
        browser_bridge=BrowserBridge(pipeline, clock),
        live_bridge=live_bridge,
        codebase=CodebaseKnowledge(pipeline),
        compressor=CitationPreservingCompressor(),
        telemetry=telemetry,
    )
    _log.info("knowledge_fabric.ready", event_type="lifecycle", use_models=use_models)
    return components
