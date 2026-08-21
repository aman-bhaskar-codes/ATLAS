# Current Memory Retrieval — Audit (Prompt 3, §1)

The memory side of the knowledge stack (`atlas/memory/`).

## 1. Stores

| Store | Implementation | Contents |
| --- | --- | --- |
| Semantic memory | SQLite + Chroma `atlas_semantic` | `SemanticFact`s with salience; dense search |
| Episodic memory | SQLite + Chroma `atlas_episodes` | `Episode`s; keyword search + vector search; embedding via background `EmbeddingWorker` |
| Knowledge chunks | SQLite + Chroma `atlas_knowledge` | document chunks from `KnowledgeStore` |
| User model | SQLite | rendered profile block |
| Trajectory / skills / world-state | SQLite | execution traces, promoted skills |

## 2. `Retriever` (`memory/retrieval.py`) — the read path before every decision

```
retrieve(query, terms, task_id, correlation_id)
  1. RetrievalCache check (TTL 30 s; invalidated on any memory write)
  2. parallel: sem.semantic_search(k=15) | epi.keyword_search(15)
               | epi.semantic_search(10, min_salience=0.3) | user_model.render()
               | knowledge_store.search(5)
  3. facts: RRF rank + 0.1·salience boost
     episodes: RRF(vector rank + 0.5·keyword rank) + 0.1·salience
  4. token knapsack (budget 1500; knowledge ≤ 500)
  5. RetrievedContext{user_model, facts, recent_episodes, knowledge_chunks, token_estimate}
```

RRF constant k=60. Cache hit target < 1 ms; miss target < 200 ms.

## 3. How the orchestrator uses it

- `Orchestrator._build_prior_knowledge()` renders retrieved lessons/skills into
  the planner prompt as **advisory** context — retrieved knowledge never relaxes
  constraints (planner.py documents this explicitly).
- `ContextBuilder` uses `Retriever.retrieve()` output for per-turn context.
- `MemoryKnowledgeSource` wraps the Retriever as a `KnowledgeProvider` so the
  knowledge fan-out can consult memory like any other source.

## 4. Consolidation link

- `KnowledgePlatform` writes every lookup back as an `Episode`
  (`kind=OBSERVATION, salience=0.4`) so consolidation can distill what was learned.
- `consolidation.py` / `experience_extractor.py` distill episodes → facts/skills
  offline. **No provenance/citation requirement gates promotion today** (§75-78
  requires: only validated, cited knowledge may promote to trusted semantic memory).

## 5. What Prompt 3 adds on top (not replacing)

1. Memory results fused with external RAG results **with distinct provenance**
   (§45-47): a `MEMORY` evidence class separate from `WEB_PAGE`/`LOCAL_FILE`
2. `MEMORY_RAG` mode — user-private questions answered from memory+documents
3. Research continuation: prior `ResearchSession` state surfaced through
   episodic retrieval (§137)
4. Promotion gate: consolidation must see source IDs / evidence mappings before
   a summary enters trusted semantic memory (§123)
