"""Knowledge Fabric — ONE pipeline for all knowledge (§0-§2).

SOURCE → INGEST → NORMALIZE → ENRICH → INDEX → RETRIEVE → RERANK →
VERIFY → SYNTHESIZE → CITE → STORE EXPERIENCE → EVALUATE → IMPROVE.

Local files, browsed pages, provider results, memory and codebase all
normalize into KnowledgeDocument and share this pipeline. Evaluation
(atlas.evaluation.rag_*) and training (atlas.training) consume the
fabric's stores OFFLINE — they are never imported from the hot path.
"""

from atlas.knowledge.bm25 import BM25Index
from atlas.knowledge.browser_bridge import BrowserBridge
from atlas.knowledge.cache import QueryResultCache
from atlas.knowledge.chunking import chunk_parsed
from atlas.knowledge.citations import CitationEngine
from atlas.knowledge.codebase import CodebaseKnowledge
from atlas.knowledge.compression import CitationPreservingCompressor, CompressedSummary
from atlas.knowledge.domain import (
    Citation,
    Claim,
    Contradiction,
    Evidence,
    FabricChunk,
    FailureCause,
    IngestionJob,
    KnowledgeDocument,
    QueryRoute,
    RAGMode,
    SecurityStatus,
    SourceType,
)
from atlas.knowledge.engine import KnowledgeFabric
from atlas.knowledge.evidence import ClaimExtractor, ClaimVerifier, ContradictionDetector, EvidenceSelector
from atlas.knowledge.ingestion import IngestionPipeline
from atlas.knowledge.injection import scan_for_injection
from atlas.knowledge.memory_fusion import MemoryBridge
from atlas.knowledge.parsers import parse_content
from atlas.knowledge.providers_bridge import LiveBridge
from atlas.knowledge.reranking import FeatureReranker, RerankWeights
from atlas.knowledge.research import (
    ResearchBudget,
    ResearchGraph,
    ResearchOutcome,
    ResearchPlanner,
    ResearchQuestion,
    ResearchRunner,
    ResearchSession,
)
from atlas.knowledge.retrieval import Candidate, HybridRetriever, RetrievalResult
from atlas.knowledge.router import QueryPlan, QueryRouter
from atlas.knowledge.store import FabricStore
from atlas.knowledge.synthesis import AnswerSynthesizer, FabricAnswer
from atlas.knowledge.telemetry import RagRecord, RagTelemetry

__all__ = [
    "AnswerSynthesizer",
    "BM25Index",
    "BrowserBridge",
    "Candidate",
    "Citation",
    "CitationEngine",
    "CitationPreservingCompressor",
    "Claim",
    "ClaimExtractor",
    "ClaimVerifier",
    "CodebaseKnowledge",
    "CompressedSummary",
    "Contradiction",
    "ContradictionDetector",
    "Evidence",
    "EvidenceSelector",
    "FabricAnswer",
    "FabricChunk",
    "FabricStore",
    "FailureCause",
    "FeatureReranker",
    "HybridRetriever",
    "IngestionJob",
    "IngestionPipeline",
    "KnowledgeDocument",
    "KnowledgeFabric",
    "LiveBridge",
    "MemoryBridge",
    "QueryPlan",
    "QueryResultCache",
    "QueryRoute",
    "QueryRouter",
    "RAGMode",
    "RagRecord",
    "RagTelemetry",
    "RerankWeights",
    "ResearchBudget",
    "ResearchGraph",
    "ResearchOutcome",
    "ResearchPlanner",
    "ResearchQuestion",
    "ResearchRunner",
    "ResearchSession",
    "RetrievalResult",
    "SecurityStatus",
    "SourceType",
    "chunk_parsed",
    "parse_content",
    "scan_for_injection",
]
