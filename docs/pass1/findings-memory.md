# Phase 0 Findings — Memory, World State, Experience

Verified by reading call sites. Every claim below has a file:line proof.

## Tiers are genuinely separate (good)

Four distinct stores, confirmed — not a collapsed vector store.

| Tier | Class | Backing store |
|---|---|---|
| Working | `WorkingMemory` `memory/working.py:12` | in-process `deque`, `maxlen=100`, **not persisted** |
| Episodic | `EpisodicMemory` `memory/episodic.py:31` | SQLite `episodes` + Chroma `atlas_episodes` |
| Semantic | `SemanticMemory` `memory/semantic.py:37` | SQLite `semantic_facts` + Chroma `atlas_semantic` |
| User model | `UserModel` `memory/user_model.py:31` | SQLite `user_model`, versioned sections |

Proof of separation: `memory/vectorstore.py:44-52` creates three distinct Chroma
collections on one `PersistentClient`; ID namespaces disjoint (`ep_`, `kc_`).

User model hard cap: `_MAX_CHARS = 3200` (~800 tokens) `user_model.py:29`, enforced :228.

## Retrieval is genuinely parallel (good)

`memory/retrieval.py:91-105` — `asyncio.create_task` × 4-5 then `asyncio.gather`.
5 queries when knowledge store present, 4 otherwise.

- RRF constant `_RRF_K = 60` :28
- Facts: `rrf = 1.0 / (_RRF_K + rank)`, then `+ 0.1 * salience` :169-171
- Episodes: semantic `1.0/(K+r)` + sparse `0.5/(K+r)` + `0.1*salience` :190-196
- Budget `token_budget: int = 1500` :39 (bootstrap passes nothing → 1500 live)
- Split :116-117 — knowledge `min(500, budget//3)` = 500, memory = 1000
- Token estimate `len(text) // 4` :204

**Correction to earlier belief:** selection is NOT a knapsack. `_pack` :207-229 is
greedy break-on-first-overflow, and facts+episodes share one `used` counter, so
facts can starve episodes entirely.

## P0 — The learning loop is severed by ONE missing call

`TrajectoryStore.record_experience_application()` `memory/trajectory_store.py:557`
has **zero callers in src/**. Cascade:

1. `reuse_count` stays 0 forever
2. `orchestrator.py:233` queries `ExperienceQuery(min_confidence=0.65, min_reuse_count=1, limit=5)`
   → `min_reuse_count=1` is **unsatisfiable** → always returns 0 rows
3. `skills_promotion.py:66` gate `reuse_count >= 2` never opens → no skill ever created
4. `experience_applications` table never written
5. `experiences.success_rate` / `avg_improvement_ms` (updated at :587 from that table)
   stay at defaults

So experiences are extracted, LLM-summarised, persisted — and never read back into
planning. The write path works; the read path is filtered to nothing.

## P0 — Second, independent skill break

`SkillStore.record_application()` `memory/skills.py:124` also has zero src/ callers.
Thresholds `PROMOTION_MIN_APPLICATIONS = 3`, `PROMOTION_MIN_SUCCESS_RATE = 0.7` :26-29
can therefore never be met. `orchestrator.py:227` calls `active_skills(limit=5)` on the
live planning path — always `[]`.

## P0 — World state is a write-never table

`memory/world_state.py` — `upsert()` :36 / `patch()` :46 have **no production caller**
(grep for `.upsert(`/`.patch(` across src/ returns only the internal self-call at :50,
Chroma upserts, and unrelated Google HTTP `client.patch`).

Read sites are real and on the live path but always yield empty:
- `orchestrator.py:241` `await self._world_state.to_prompt_fragment(limit=8)`, guarded
  `if fragment:` :242 → contributes nothing
- `routes_learning.py:204,218` → returns `[]`

Class docstring confirms by design: "No inference, no background syncing — writers are
explicit (tools, platforms)." No writer was ever added. **Phase 15 must add event-driven
population.**

## P0 — Strategies subsystem 100% unwired

`StrategyStore` is constructed (`bootstrap/memory.py:109`), handed to `Atlas`
(`app.py:576`), but **never passed into the orchestration builder** (which gets
`skill_store` :482 and `world_state` :483 only). Zero `save`/`active_for`/`record_outcome`
calls. Also gated on `eval_score >= 0.7` `strategies.py:109-110`, sourced from
`evaluation_results` — a table with **no writer at all**.

## Dead / write-only tables (findings for the state-ownership model)

| Table | Status |
|---|---|
| `decision_traces` | WRITE-NEVER — `save_decision_trace` :251 zero callers; `orchestrator.py:286` hardcodes `()` |
| `failure_records` | WRITE-NEVER — `save_failure_record` :351 zero callers; `orchestrator.py:287` hardcodes `()`; `get_failure_patterns(min_occurrences=3)` permanently empty |
| `experience_applications` | WRITE-NEVER — severs reuse tracking |
| `strategies` | WRITE-NEVER |
| `world_state` | WRITE-NEVER |
| `consolidation_proposals` | WRITE-ONLY, zero readers — nightly consolidator LLM output discarded |
| `memory_archive` | WRITE-ONLY, and never written (Pruner unscheduled) |
| `evaluation_results` | No src/ reference beyond DDL |

## Latency: what blocks the user response

**Blocking (critical path):**
- `orchestration/recorder.py` — **three sequential** `await self._epi.record(...)` per
  reasoning step (:22 thought, :34 action, :47 observation), each an INSERT +
  `await commit()` on one shared aiosqlite connection
- `reasoning.py:383` — `await self._checkpoints.save(...)` per step
- `orchestrator.py:194-200` — `await self._save_trajectory(...)` **before** the
  `task.completed` event :202 and `return` :209
- `orchestrator.py:140` — `_build_prior_knowledge()`, 3 sequential awaits (:227/:232/:241)

**Backgrounded (correct):** episodic bus publish :194, embed :89, experience extraction
`orchestrator.py:319`, trajectory bus publish :143, embedder queue :119.

**Hidden amplifier:** bus handlers `EpisodicMemory._on_orchestrator_event` (`episodic.py:54`)
and `SemanticMemory._on_task_event` (:59) subscribed at `app.py:169-172`. The former writes
an **additional episode row for every orchestrator event** → systematic double-write into
`episodes` (one from recorder.py, one from the bus).

## Observation truncation limits (already bounded — Phase 14 partly done)

| Limit | Location | Applies to |
|---|---|---|
| 1000 | `recorder.py:53`, `reasoning.py:305` | episode row / trajectory record |
| 500 | `reasoning.py:319` | working memory |
| 300 | `prompt_builder.py:35` | **next step prompt** |
| 200 | `context_builder.py:54`, `memory/types.py:80,86` | built context |
| 1500 | `reasoning.py:389` | checkpoint history summary |
| 4000 | `orchestrator.py:246` | whole `prior_knowledge` blob |

**Unbounded gap:** `build_step_prompt` `prompt_builder.py:22` iterates the **entire**
history with no turn cap (only per-item `[:300]`). `ContextCompactor.compact()`
`context_engine.py:57` is **never called**; `ContextBudget(total=6000, max_history_turns=8)`
:24-32 is declared but unenforced. Only bound is `history[-3:]` on replan
(`reasoning.py:224`, :352). **Phase 24 must enforce this.**

## Other defects

- `Retriever.set_events` :51 never called; `bootstrap/memory.py:82-87` omits `events=`
  → `memory.retrieved` events :126-138 **never fire** (observability blind spot)
- Retrieval cache never invalidated on write (only `cli.py:1197`), contradicting the
  module docstring :9 → writes invisible for 30s TTL
- `Pruner` never scheduled — the only `register_job` is `app.py:510` (consolidation).
  `episodes` grows unbounded; `_MAX_EPISODES = 20_000` unenforced
- Checkpoints not pruned on success (only cancel :403 / error :428) → `interrupted_tasks`
  over-reports completed tasks
- `semantic.py:144` `extract_facts_llm` is a **TODO stub returning []**
- Docstring contradiction: `recorder.py` claims semantic memory is written "solely by
  consolidation", but `SemanticMemory._on_task_event` :59 auto-commits facts on
  `task.completed` when `confidence >= 0.8` :76 via 5 hardcoded regexes :99
- Layer violation: `episodic.py:320,323` reaches into `self._embedding_worker._embedder`
  and `._vector_store` privates
- `FallbackEmbedder` `embedder.py:66` never used; `OllamaEmbedder` raises hard on zero
  vector :52-53 → **if Ollama is down, embedding fails with no degradation**
- Dead duplicate file `orchestration/managers/checkpoint.py` (38 lines), no importer
- `knowledge_store.py:250-254` PDF ingestion is a plain-text-read stub

## Resume

`orchestration/resume.py` — `assess_resume_safety` :30 requires **every** remaining step's
tool to declare `meta.idempotent`; `try_resume` :84 has exactly one caller,
`interfaces/cli.py:129-134`. `app.py:190-196` crash recovery is a separate fail-clean path
that does not resume. Correct in principle, reachable only from CLI.
