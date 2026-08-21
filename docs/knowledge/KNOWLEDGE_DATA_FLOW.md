# Knowledge Data Flow — Audit (Prompt 3, §1)

Current flows vs. the target fabric flow.

## 1. Current flows (three disconnected pipes)

```
PIPE A — live question
  KnowledgeQuery → KnowledgeRouter → provider fan-out (rss/wiki/arxiv/ddg/…)
  → KnowledgeItem[] → trust×recency sort → LLM summarize → Answer → episodic

PIPE B — local documents
  file → KnowledgeStore/DocumentProcessor → chunks → embed → Chroma+SQLite
  → Retriever.search(5) → RetrievedContext.knowledge_chunks → planner context

PIPE C — browser research
  seed URL → CrawlerEngine → Article[] → returned to caller → (lost)
```

The pipes share nothing: A never indexes what it fetches, B is never fed by C,
C's output is never retrievable by A or B.

## 2. Target fabric flow (Prompt 3, §0 — one canonical pipeline)

```
SOURCES                     CANONICAL PIPELINE                     CONSUMERS
─────────                   ──────────────────                     ─────────
local files     ┐
browsed pages   │           ┌─► KnowledgeDocument (§3)
fetched pages   ├─ INGEST ──┤      │ normalize + provenance + trust
search results  │           │      ▼
RSS/arXiv/wiki  │           │   ENRICH (injection scan, entities, structure)
github          │           │      ▼
memory episodes ┤           │   CHUNK (structure-aware) → IngestionJob (§23)
codebase files  ┘           │      ▼
                            │   INDEX: vector + BM25 + metadata (+graph later)
                            │      ▼
USER QUESTION ─ QUERY ROUTER (§12) ─► RETRIEVE hybrid RRF (§15)
                                      → RERANK (§26)
                                      → EVIDENCE (§4) + CONTRADICTION (§30)
                                      → CLAIMS + VERIFY (§32-33)
                                      → SYNTHESIZE (evidence-first, §52)
                                      → CITE (built from evidence, §34)
                                      → ANSWER (+ uncertainty, §54)
                                           │
                     ┌─────────────────────┼──────────────────────┐
                     ▼                     ▼                      ▼
              STORE EXPERIENCE       EVALUATE (async)      FAILURE TAXONOMY
              (research sessions,    (metrics, datasets,   (§58 — machine-
               episodes, feedback)    baseline-vs-candidate) readable causes)
                     │                     │                      │
                     └────────► IMPROVE RETRIEVAL ◄───────────────┘
                                (training data → offline
                                 retriever/reranker/router
                                 candidates → promotion gate)
```

## 3. Design decisions adopted for the build

1. **Home of the fabric**: extend `atlas/knowledge/` into the fabric package
   (currently imported by nothing — free to position). Because the fabric must
   consume providers and the browser platform, it sits ABOVE `atlas.capabilities`
   in `importlinter.ini` (orchestration > knowledge > capabilities > memory).
   Legacy `KnowledgePlatform` stays untouched as the fast path.
2. **Canonical object**: new `KnowledgeDocument` (§3 fields); existing
   `KnowledgeStore` tables gain the fabric fields via a new migration-free
   fabric table set (`fabric_documents`, `fabric_chunks`, `fabric_evidence`)
   to avoid breaking the existing API.
3. **Browser bridge**: `Article`/fetched page → `KnowledgeDocument` →
   same ingestion pipeline. Crawler gets an optional `on_document` sink.
4. **Memory fusion**: fabric calls the existing `Retriever` and tags results
   with `source_type=MEMORY`; provenance kept separate from external evidence.
5. **Free-first**: BM25 implemented locally (no dependency); evaluation is
   ATLAS-native with Ragas-compatible metric names; ragas optional if installed.
6. **Hot-path rule**: evaluation + training NEVER run on the request path (§59, §130).
