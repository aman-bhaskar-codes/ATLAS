# Current RAG Pipeline — Audit (Prompt 3, §1)

The three paths that exist today, exactly as implemented.

## 1. Ingest path (local documents)

Two independent entry points, both ending in the same dual store:

```
file path ──► DocumentProcessor.ingest_file/directory   (knowledge/document_processor.py)
              │ 22 extensions; paragraph-aware chunks (1000 chars, 200 overlap)
              │ embed(text[:2000]) via Embedder
              ▼
file path ──► KnowledgeStore.ingest_document              (memory/knowledge_store.py)
              │ sha256(file) dedupe against knowledge_documents.file_hash
              │ char-window chunks (512 tok ≈ 2048 chars, 50 tok overlap)
              │ parallel embed+index; SQL knowledge_chunks + Chroma atlas_knowledge
              ▼
          SQLite(knowledge_documents, knowledge_chunks) + Chroma(atlas_knowledge)
```

- Incremental: document-level only (file_hash). No chunk-level, no pipeline versioning.
- No state machine: ingestion is a straight-through function call.
- No prompt-injection screening, no authority/freshness extraction at ingest.
- API entry: `POST /api/v1/knowledge/ingest` and `/ingest/upload` (routes_knowledge.py).

## 2. Retrieve path (dense-only)

```
query ──► KnowledgeStore.search(query, limit)
          embed(query) → Chroma search_knowledge(k=2×limit)
          → join chunk rows for metadata → [{chunk_id, content, score, title, source_path, ...}]

query ──► Retriever.retrieve(query)            (memory/retrieval.py)
          5 parallel lookups: semantic facts (dense 15), episode keywords (15),
          episode vectors (10), user model, knowledge store (5)
          → RRF fusion per type → token knapsack (1500, ≤500 for knowledge)
          → RetrievedContext (cached 30 s)
```

- There is **no BM25/lexical path** for documents and **no rerank stage**.
- Score used is raw cosine similarity; no authority/freshness features at chunk level.

## 3. Synthesize path (live questions)

```
KnowledgeQuery ──► KnowledgeRouter.classify      (deterministic LIVE cues, else LLM JSON)
             ──► KnowledgePlatform.obtain_knowledge
                  STATIC → parametric provider
                  MEMORY → memory provider (Retriever)
                  LIVE/MIXED → gather(memory + official [+ web]) in parallel
                  → rank by 0.7·trust + 0.3·recency → top max_sources
                  → ModelGateway SUMMARIZATION prompt
                  → Answer{text, confidence, sources}
                  → episodic write-back
```

## 4. Properties vs. Prompt-3 requirements

| Requirement | Status |
| --- | --- |
| Evidence-first answers (§4) | **No** — `Evidence` model exists but unused; LLM synthesizes from snippets |
| Structured citations (§34) | **No** — sources attached at Answer level only, not claim-level |
| Contradiction detection (§30) | **No** — prompt says “if sources conflict, say so”, nothing checks |
| Hybrid BM25+dense (§15) | **No** — dense only |
| Rerank (§26-29) | **No** — single trust×recency sort of items |
| Claim verification (§33) | **No** |
| RAG modes (§55) | **No** — one mode; intents are 4-way only |
| Failure taxonomy (§58) | **No** — provider errors logged and dropped (`_safe_search` → []) |
