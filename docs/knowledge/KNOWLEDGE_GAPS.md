# Knowledge Gaps — Audit (Prompt 3, §1)

Gap → spec section → consequence. This is the build list for the fabric.

## A. Representation

| Gap | Spec | Consequence today |
| --- | --- | --- |
| Three document representations (`KnowledgeItem` snippet, `memory.Document`, `Chunk`) | §3 | browser/PDF/API results can't share one pipeline |
| No fabric-level `Evidence` object in use (model exists, unused) | §4 | citations are LLM output, not built from evidence |
| No `SourceType` taxonomy | §5 | routing/trust can't key on source class |
| No `Claim` model | §32-33 | nothing can be verified or disputed |

## B. Retrieval

| Gap | Spec | Consequence |
| --- | --- | --- |
| Dense-only retrieval; no BM25 | §15-16 | keyword/exact-match queries miss; no fallback when embeddings unavailable (§138) |
| No cross-index RRF for documents | §15 | only memory-side RRF exists |
| No rerank stage | §26-29 | no authority/freshness/diversity-aware final ordering |
| No multi-stage narrowing (50→20→10→5-8) | §17 | context packing is budget-only |
| No query decomposition / bounded rewrites | §13-14 | multi-hop questions answered in one shot |
| 4-way intent only | §12 | no RESEARCH/CODEBASE/PRIVATE/MULTI_HOP routes |

## C. Verification & synthesis

| Gap | Spec | Consequence |
| --- | --- | --- |
| No contradiction detection | §30 | conflicting sources silently summarized |
| No claim extraction/verification | §32-33 | answers not grounded per-claim |
| No CitationEngine | §34 | URLs come from the model → hallucination risk |
| No evidence-first synthesis | §52-53 | synthesis prompt sees snippets, not quotes |
| No honest “insufficient evidence” contract | §54 | low confidence returned as an answer anyway |
| No RAG modes | §55 | one behavior for every question type |
| No failure taxonomy for retrieval | §58 | failures logged, not classified or learned from |

## D. Browser & research

| Gap | Spec | Consequence |
| --- | --- | --- |
| Crawler articles never indexed | §6-8 | browsed knowledge is lost after the task |
| No `ResearchSession` persistence | §9 | “continue yesterday's research” impossible |
| No injection screening of fetched text | §10, §139 | web text enters prompts as-is |
| No research graph/questions/planner | §38-44, §75-82 | no bounded multi-step research |
| No information-gain / stop conditions | §40-41 | crawl budget is the only stopping rule |

## E. Learning & evaluation

| Gap | Spec | Consequence |
| --- | --- | --- |
| No RAG evaluation layer (no Ragas, no metrics) | §59-66 | pipeline quality unmeasured |
| No evaluation datasets / golden benchmarks | §63-65 | no regression detection |
| No training-data capture / retriever-reranker training infra | §67-74 | no retrieval improvement loop |
| No model/adapter registry & promotion gate | §101-103 | nothing to promote candidates against |
| No codebase knowledge provider | §48-49 | ATLAS can't RAG over its own repo |
| No memory↔RAG provenance separation | §45-47 | memory and web evidence blur together |
| No promotion gate on consolidation | §75-78 | unprovenanced summaries could become “facts” |

## F. Engineering

| Gap | Spec | Consequence |
| --- | --- | --- |
| `atlas.knowledge` outside import-linter layers | §132 | fabric layering unenforced |
| No ingestion state machine / pipeline versioning | §23-25 | no partial-failure recovery, no reindex-on-parser-change |
| No query-result cache separate from document cache | §50 | `ResearchCache` conflates both |
| No RAG telemetry | §98 | no latency/miss-rate visibility |
