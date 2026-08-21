# Current ATLAS Knowledge Architecture — Audit (Prompt 3, §1)

Grounded inventory of everything knowledge-related in the codebase before the
Knowledge Fabric build. Nothing here should be rebuilt — it should be connected.

## 1. Component inventory

| Component | Location | Role |
| --- | --- | --- |
| `KnowledgeProvider` protocol | `capabilities/providers/knowledge/base.py` | `query → list[KnowledgeItem]`; keeps generic Provider protocol so dispatcher/health/registry work |
| Providers ×9 | `capabilities/providers/knowledge/` | arxiv, brave, duckduckgo, github_releases, rss, tavily, wikipedia, memory_source, parametric |
| `KnowledgeQuery/KnowledgeItem/Evidence/Answer` | `capabilities/domain/knowledge.py` | domain models; `Evidence` is defined but NOT used in synthesis today |
| `KnowledgeRouter` | `capabilities/platforms/knowledge_router.py` | deterministic live-cues + LLM classify → STATIC/MEMORY/LIVE/MIXED |
| `KnowledgePlatform.obtain_knowledge()` | `capabilities/platforms/knowledge_platform.py` | intent → parallel fan-out → trust×recency rank → LLM summarize → `Answer`; writes lookup back to episodic memory |
| `KnowledgeStore` | `memory/knowledge_store.py` | document indexing: SQL `knowledge_documents`/`knowledge_chunks` + Chroma `atlas_knowledge`; hash dedupe; parallel embedding; vector search + metadata enrichment |
| `DocumentProcessor` | `knowledge/document_processor.py` | local-file ingestion (22 extensions), paragraph-aware overlapping chunking |
| `ResearchCache` | `knowledge/research_cache.py` | vector-backed query-result cache (7-day TTL) |
| `Retriever` | `memory/retrieval.py` | hybrid RRF over semantic facts + episodes + knowledge chunks; token budget; `RetrievalCache` (TTL, invalidated on writes) |
| `ChromaVectorStore` | `memory/vectorstore.py` | 3 collections: `atlas_semantic`, `atlas_episodes`, `atlas_knowledge`; cosine-similarity hits |
| `OllamaEmbedder` + `EmbeddingWorker` | `memory/embedder.py` | bge-m3 via ModelGateway (free/local); batched background episode embedding |
| Browser research | `capabilities/browser/research/` | `CrawlerEngine` (bounded depth/budget), `Reader` (naive HTML→text), `SourceRanker` (domain trust map) |
| API surface | `interfaces/api/routes_knowledge.py` | `/ingest`, `/ingest/upload`, `/search`, `/documents` CRUD |
| Config | `config/knowledge_sources.yaml` | official vendor RSS feeds + provider preferences (free-first order) |
| Orchestrator hook | `orchestration/orchestrator.py` `_build_prior_knowledge()` | advisory retrieval into planner context; never relaxes constraints |

## 2. Data stores

- **SQLite** (`infra/db.py`): `knowledge_documents` (id, title, source_path, source_type, chunk_count, file_hash, indexed, ts), `knowledge_chunks` (chunk_id, document_id, content, index, embedding_id, metadata_json)
- **Chroma** (`.atlas/chroma`): embeddings for facts, episodes, knowledge chunks
- **No BM25/lexical index exists anywhere** — retrieval is dense-only

## 3. Trust model (current)

- `KnowledgePlatform._TRUST = {local: 0.9, official: 1.0, web: 0.6, model: 0.5}`
- `SourceRanker` — domain allowlist (github.com, docs.python.org, MDN, wikipedia, arxiv) → 1.0; .edu/.gov → 0.9; .org → 0.7; else 0.5
- Provenance rides on every `KnowledgeItem` (`capabilities.domain.common.Provenance`)

## 4. Layering

`importlinter.ini` layers: interfaces > diagnostics > evaluation > orchestration >
capabilities > memory > intelligence > safety > tools > control > perception > infra.
**`atlas.knowledge` is NOT in the contract today** — it must be added when the
fabric is built (it needs to sit above `capabilities` so it can consume providers
and the browser platform, and below `orchestration`/`interfaces`).

## 5. What is strong (reuse, don't rebuild)

1. Provider fan-out + dispatcher/safety gating pattern
2. `KnowledgeStore` SQL+vector dual store with hash dedupe
3. `Retriever` RRF fusion + token knapsack
4. `ModelGateway` for every model call (metered, capability-typed)
5. Episodic write-back of lookups (consolidation learns from them)
6. Bounded crawler with URL trust ranking
