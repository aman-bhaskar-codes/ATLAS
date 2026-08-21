"""Knowledge Fabric — canonical domain (§3, §4, §5).

WHY one canonical model: every source (local files, browsed pages, search
results, RSS/arXiv, memory, codebase) normalizes into the SAME KnowledgeDocument
so one pipeline can ingest, index, retrieve, verify, and cite all of it.
Evidence — not the LLM — is the basis of citations: a quote pinned to a
document, chunk, and location.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


# ── Source taxonomy (§5) ──────────────────────────────────────────────────
class SourceType(StrEnum):
    LOCAL_FILE = "local_file"
    DOCUMENT = "document"
    WEB_PAGE = "web_page"
    BROWSER_PAGE = "browser_page"
    GITHUB = "github"
    RSS = "rss"
    ARXIV = "arxiv"
    SEMANTIC_SCHOLAR = "semantic_scholar"
    CROSSREF = "crossref"
    WOLFRAM_ALPHA = "wolfram_alpha"
    PUBLIC_API = "public_api"
    EMAIL = "email"
    CALENDAR = "calendar"
    MEMORY = "memory"
    EXPERIENCE = "experience"
    KNOWLEDGE_GRAPH = "knowledge_graph"
    USER_PROVIDED = "user_provided"


# Trusted-by-construction sources get a higher authority floor (§35).
AUTHORITY_FLOOR: dict[SourceType, float] = {
    SourceType.LOCAL_FILE: 0.8,
    SourceType.MEMORY: 0.6,
    SourceType.EXPERIENCE: 0.6,
    SourceType.USER_PROVIDED: 0.7,
    SourceType.ARXIV: 0.8,
    SourceType.SEMANTIC_SCHOLAR: 0.8,
    SourceType.CROSSREF: 0.8,
    SourceType.GITHUB: 0.7,
    SourceType.RSS: 0.7,
}


class SecurityStatus(StrEnum):
    """§118 — every document carries a security verdict."""

    SAFE = "SAFE"
    SUSPICIOUS = "SUSPICIOUS"  # injection markers found; usable as DATA only
    BLOCKED = "BLOCKED"  # never enters any context


class IngestionState(StrEnum):
    """§23 — ingestion is a bounded state machine, not a straight-through call."""

    DISCOVERED = "DISCOVERED"
    FETCHING = "FETCHING"
    PARSING = "PARSING"
    NORMALIZING = "NORMALIZING"
    CHUNKING = "CHUNKING"
    EMBEDDING = "EMBEDDING"
    INDEXING = "INDEXING"
    READY = "READY"
    FAILED = "FAILED"


class RAGMode(StrEnum):
    """§55 — the fabric answers differently per question class."""

    DIRECT = "DIRECT"  # parametric only; nothing to retrieve
    RAG = "RAG"  # local/indexed documents
    HYBRID = "HYBRID"  # memory + documents + live
    DEEP_RESEARCH = "DEEP_RESEARCH"  # bounded multi-source research
    CODEBASE_RAG = "CODEBASE_RAG"
    MEMORY_RAG = "MEMORY_RAG"  # private/user data only
    MULTI_HOP_RAG = "MULTI_HOP_RAG"
    BROWSER_RAG = "BROWSER_RAG"
    RESEARCH_GRAPH = "RESEARCH_GRAPH"


class QueryRoute(StrEnum):
    """§12 — where the answer must come from."""

    MEMORY = "MEMORY"
    STATIC_KNOWLEDGE = "STATIC_KNOWLEDGE"
    LIVE = "LIVE"
    RESEARCH = "RESEARCH"
    COMPUTATIONAL = "COMPUTATIONAL"
    MULTI_HOP = "MULTI_HOP"
    PRIVATE_KNOWLEDGE = "PRIVATE_KNOWLEDGE"
    CODEBASE = "CODEBASE"
    MIXED = "MIXED"


class FailureCause(StrEnum):
    """§58/§126 — machine-readable retrieval failure taxonomy."""

    RETRIEVAL_MISS = "RETRIEVAL_MISS"
    RETRIEVAL_NOISE = "RETRIEVAL_NOISE"
    WRONG_SOURCE = "WRONG_SOURCE"
    PARSER_FAILURE = "PARSER_FAILURE"
    CHUNKING_FAILURE = "CHUNKING_FAILURE"
    EMBEDDING_MISMATCH = "EMBEDDING_MISMATCH"
    RERANK_FAILURE = "RERANK_FAILURE"
    CONTEXT_OVERFLOW = "CONTEXT_OVERFLOW"
    EVIDENCE_CONFLICT = "EVIDENCE_CONFLICT"
    CITATION_FAILURE = "CITATION_FAILURE"
    ANSWER_GROUNDING_FAILURE = "ANSWER_GROUNDING_FAILURE"
    FRESHNESS_FAILURE = "FRESHNESS_FAILURE"
    BROWSER_EXTRACTION_FAILURE = "BROWSER_EXTRACTION_FAILURE"


class ClaimStatus(StrEnum):
    SUPPORTED = "SUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    UNSUPPORTED = "UNSUPPORTED"
    DISPUTED = "DISPUTED"


class AdapterState(StrEnum):
    """§101 — lifecycle of learned retrieval/rerank/router adapters."""

    EXPERIMENTAL = "EXPERIMENTAL"
    VALIDATED = "VALIDATED"
    ACTIVE = "ACTIVE"
    DEPRECATED = "DEPRECATED"


class FeedbackLabel(StrEnum):
    """§125 — user feedback becomes training/evaluation evidence."""

    CORRECT = "correct"
    INCORRECT = "incorrect"
    MISSING_SOURCE = "missing_source"
    WRONG_SOURCE = "wrong_source"
    OUTDATED = "outdated"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class ResearchQuestionStatus(StrEnum):
    """§76 — research questions are tracked, not dropped."""

    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    ANSWERED = "ANSWERED"
    BLOCKED = "BLOCKED"
    DISPUTED = "DISPUTED"


# ── Pipeline versioning (§25) ─────────────────────────────────────────────
# Bumping any of these marks existing chunks stale for re-indexing.
PARSER_VERSION = "1"
CHUNKER_VERSION = "1"
EMBEDDING_VERSION = "1"
PIPELINE_VERSION = f"p{PARSER_VERSION}.c{CHUNKER_VERSION}.e{EMBEDDING_VERSION}"


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


# ── Canonical objects ─────────────────────────────────────────────────────
class KnowledgeDocument(BaseModel):
    """§3 — the ONE representation every source normalizes into."""

    model_config = {"frozen": True}

    document_id: str
    source_id: str  # provider/path/session that produced it
    source_type: SourceType
    title: str
    uri: str = ""
    canonical_uri: str = ""  # dedupe key for same-content URIs
    content: str = ""
    content_type: str = "text/plain"
    language: str = "en"
    author: str = ""
    published_at: datetime | None = None
    retrieved_at: datetime
    modified_at: datetime | None = None
    content_hash: str = ""
    authority: float = 0.5
    trust_score: float = 0.5
    freshness: float = 0.5  # 1.0 = just published/retrieved, decays with age
    license: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    security_status: SecurityStatus = SecurityStatus.SAFE
    security_flags: tuple[str, ...] = ()
    pipeline_version: str = PIPELINE_VERSION

    def with_hash(self) -> KnowledgeDocument:
        if self.content_hash:
            return self
        return self.model_copy(update={"content_hash": content_hash(self.content)})


class FabricChunk(BaseModel):
    """A structure-aware chunk of a KnowledgeDocument."""

    model_config = {"frozen": True}

    chunk_id: str
    document_id: str
    content: str
    heading: str = ""
    chunk_index: int = 0
    total_chunks: int = 1
    char_start: int = 0
    char_end: int = 0
    token_estimate: int = 0
    kind: str = "text"  # text | table | code
    embedding_id: str | None = None


class Evidence(BaseModel):
    """§4 — a quote pinned to a source. Citations are BUILT from these."""

    model_config = {"frozen": True}

    evidence_id: str
    document_id: str
    chunk_id: str
    source: SourceType
    quote: str
    location: str = ""  # e.g. "§3", "line 42", "para 2"
    uri: str = ""
    title: str = ""
    retrieved_at: datetime
    authority: float = 0.5
    confidence: float = 0.5
    provenance: dict[str, Any] = Field(default_factory=dict)
    hash: str = ""

    def with_hash(self) -> Evidence:
        if self.hash:
            return self
        return self.model_copy(update={"hash": content_hash(f"{self.document_id}:{self.quote}")})


class Claim(BaseModel):
    """§32 — a factual assertion extracted for verification."""

    model_config = {"frozen": True}

    claim_id: str
    text: str
    evidence_ids: tuple[str, ...] = ()
    status: ClaimStatus = ClaimStatus.UNSUPPORTED
    confidence: float = 0.0


class Contradiction(BaseModel):
    """§30 — two evidence sets asserting incompatible values. Never averaged."""

    model_config = {"frozen": True}

    key: str  # normalized topic, e.g. "python/latest_version"
    description: str
    evidence_id_a: str
    evidence_id_b: str
    severity: float = 0.5  # 0..1


class Citation(BaseModel):
    """§34 — rendered from Evidence only; the model never invents URLs."""

    model_config = {"frozen": True}

    index: int  # [1], [2], ... as used in the answer text
    evidence_id: str
    document_id: str
    title: str
    uri: str
    quote: str
    authority: float = 0.5


class IngestionJob(BaseModel):
    """§23 — tracked ingestion with explicit state and failure reason."""

    model_config = {"frozen": True}

    job_id: str
    source: str  # uri or path
    source_type: SourceType
    state: IngestionState = IngestionState.DISCOVERED
    document_id: str | None = None
    error: str = ""
    failure_cause: FailureCause | None = None
    pipeline_version: str = PIPELINE_VERSION
    created_ts: datetime
    updated_ts: datetime


def make_document_id(source_type: SourceType, uri_or_path: str, retrieved_at: datetime) -> str:
    raw = f"{source_type.value}:{uri_or_path}:{retrieved_at.isoformat()}"
    return f"doc_{hashlib.sha256(raw.encode()).hexdigest()[:20]}"


def make_chunk_id(document_id: str, chunk_index: int, text_head: str) -> str:
    raw = f"{document_id}:{chunk_index}:{text_head[:120]}"
    return f"chk_{hashlib.sha256(raw.encode()).hexdigest()[:20]}"


def make_evidence_id(document_id: str, quote: str) -> str:
    return f"ev_{hashlib.sha256(f'{document_id}:{quote}'.encode()).hexdigest()[:20]}"
