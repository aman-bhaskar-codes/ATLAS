# ATLAS Architecture Map

> Generated 2026-08-15 from a full source audit (`src/atlas`, 383 Python files).
> The implementation is the source of truth; this document describes what IS, not what is planned.

## System Overview

ATLAS is a local-first autonomous agent system. A single composition root (`app.py` + `bootstrap/`)
builds the full object graph and hands it to every interface (FastAPI, CLI) as the `Atlas` dataclass.
Layer boundaries are machine-enforced by import-linter (see `importlinter.ini`).

```
┌─────────────────────────────────────────────────────────────────────┐
│ INTERFACES      FastAPI (REST/SSE/WS) · Typer CLI · Notify confirmer │
├─────────────────────────────────────────────────────────────────────┤
│ DIAGNOSTICS     doctor (fail-closed self-checks)                     │
├─────────────────────────────────────────────────────────────────────┤
│ ORCHESTRATION   Orchestrator → Router → Planner → ReasoningLoop      │
│                 (OTAR) → Replanner · Verifier · Reflection ·         │
│                 Supervisor/specialist agents (DAG)                   │
├─────────────────────────────────────────────────────────────────────┤
│ CAPABILITIES    dispatcher → capability registry → platform providers│
│                 (knowledge, email, calendar, contacts, browser,      │
│                  notification, identity)                             │
├─────────────────────────────────────────────────────────────────────┤
│ MEMORY          working · episodic · semantic · user model ·         │
│                 knowledge store · trajectory/experience · retrieval  │
├─────────────────────────────────────────────────────────────────────┤
│ INTELLIGENCE    ModelGateway → capability router → model selector →  │
│                 fallback engine → inference runtime → providers      │
├─────────────────────────────────────────────────────────────────────┤
│ SAFETY          SafetyEngine (reference monitor) · TierClassifier ·  │
│                 PolicyEngine · kill switch · audit (hash-chained) ·  │
│                 sandboxes (Docker/native)                            │
├─────────────────────────────────────────────────────────────────────┤
│ TOOLS/PERCEPTION/CONTROL  filesystem · shell · app control · AX      │
├─────────────────────────────────────────────────────────────────────┤
│ INFRA           SQLite (aiosqlite) · MessageBus · config · ids ·     │
│                 metrics · tracing · scheduler · circuit breaker      │
└─────────────────────────────────────────────────────────────────────┘
```

**Import rule (enforced):** a layer may only import layers BELOW it. `infra` may not import
anything above it. `safety`/`tools` may not import provider SDKs.

## Subsystem Reference

### orchestration/
| Module | Responsibility | Key contracts |
|---|---|---|
| `orchestrator.py` | Task pipeline facade: create → context → route → plan → reason → record. Owns task row writes and in-memory cancellation tokens. Trajectory persistence + async experience extraction. | `Task`, `TaskResult` |
| `reasoning.py` | Bounded OTAR loop. Per step: model call → parse action → (verify final) or (critique → dispatch tool → observe → reflect → maybe replan). | `Thought`, `Action`, `Observation` |
| `planner.py` | LLM plan generation (JSON), zero execution. | `Plan`, `PlanStep` |
| `replanner.py` | Plan revision from failure context; bounded by `GoalState.max_replans`. | `Plan` |
| `goal.py` | `GoalState` (desired vs current state), `VerificationResult`, `Verifier` protocol, `GoalVerifier` (LLM), `NullVerifier`. | `GoalState` |
| `state.py` | Deterministic 13-state machine with explicit legal-transition table. | `TaskState` |
| `limits.py` | `ExecutionLimits` + live `LimitCounter` (steps/tools/tokens/runtime/retries). | — |
| `events.py` | Typed bus events (Orchestrator/Safety/Planning/Memory/Tool/Feedback) + `EventPublisher`. | `Event` subclasses |
| `dispatcher.py` | Routes `Action` → `SafetyEngine.guard()` → tool. The only path to tool execution. | — |
| `self_critique.py`, `reflection.py` | Pre-action critique (revise/abort), post-action reflection. | `Critique`, `ReflectionResult` |
| `agents/` | `AgentSupervisor` runs *inside* the orchestrator (gated on `agents.enabled`, off by default): decomposition → `TaskDAG` → topological-batch concurrent dispatch to role specialists (researcher/writer/coder/analyst/general) on the **shared** `ReasoningLoop` → synthesis. No second runtime and no second tool path — delegated tool calls use the same `dispatcher → SafetyEngine.guard()` funnel. Every failure path degrades to the serial single-agent pipeline. | `SubTask`, `TaskDAG`, `SubTaskResult` |

### safety/
| Module | Responsibility |
|---|---|
| `engine.py` | Reference monitor: kill-switch → classify → policy → audit → confirm (w/ code for DANGEROUS) → re-check kill-switch → execute → audit result. Secret redaction on payloads. |
| `classifier.py` | Deterministic fail-closed tiering. Hard-block matchers first; constraints may only raise tier; no match ⇒ deny. |
| `manifest.py` / `config/permissions.yaml` | Deny-by-default rule source; never env-overridable. |
| `audit.py` | Hash-chained `audit_events` (+ payloads table) with `verify_chain()`. |
| `killswitch.py` | Global halt flag. |
| `sandbox_docker.py` / `sandbox_native.py` | Tool execution isolation; Docker required outside dev. |

### memory/
| Module | Responsibility |
|---|---|
| `working.py` | In-process episode scratchpad per correlation. |
| `episodic.py` | Step-level episode log (SQLite + vectors), keyword + semantic search. |
| `semantic.py` | Consolidated facts with salience/confidence. |
| `user_model.py` | Learned user preferences, rendered into every context. |
| `knowledge_store.py` | Curated documents/chunks with hybrid search. |
| `retrieval.py` | Hybrid read path: 5 parallel queries → RRF fusion → salience boost → token-budget knapsack. `RetrievalCache` (TTL, invalidated on writes). |
| `trajectory.py` / `trajectory_store.py` | Immutable `Trajectory`, `DecisionTrace`, `FailureRecord`, `Experience` models + SQLite store with stats (reuse_count, success_rate) and supersession. |
| `experience_extractor.py` | Post-task LLM lesson extraction (0–3/trajectory, confidence floor 0.5), async. |
| `consolidation.py` / `pruning.py` | Scheduled maintenance (2 AM cron). |
| `embedder.py` / `vectorstore.py` | Ollama embeddings → Chroma collections. |

### intelligence/
| Module | Responsibility |
|---|---|
| `gateway.py` | Single egress. `infer()` (new) and `complete()` (compat adapter for ModelRequest callers). |
| `selection/router.py` → `selection/selector.py` | Required-capability routing; multi-factor ranked selection (quality .35, reliability .20, cost .15, latency .10, health .20, +local bonus). |
| `runtime/fallback.py` → `runtime/inference.py` | Ranked-chain fallback; governed attempt (budgets, telemetry, tracker). |
| `governance/` | USD budgets (daily/weekly/monthly/per-task) + `CostGovernor`. |
| `cache.py` | `SemanticCache` (embedding-keyed response cache). |
| `providers/` | Ollama (default, free tier), OpenAI-compatible (DeepSeek/GLM/Kimi/Mimo via OpenRouter), Anthropic, Gemini. No `tools=` surface yet. |
| `health/`, `observability/`, `registry/` | Provider health tracking, cost telemetry, model registry from `config/models.yaml`. |

### capabilities/
| Area | Detail |
|---|---|
| `dispatcher.py` | Wraps platforms as `_CapabilityTool` (Tool protocol); every request passes `SafetyEngine.guard()`; provider fallback + backoff; `Provenance` (LOCAL/WEB/MCP). |
| `registry/` | `Capability` enum × `CapabilitySpec` (safety_tool, operations, default_tier, requires_auth). |
| `platforms/` + `providers/` | Knowledge (RSS/Wikipedia/Arxiv/GitHub/DDG/Brave/Tavily/memory/parametric), Email (Gmail/IMAP), Calendar (Google/Caldav), Contacts (Google People). |
| `browser/` | Domain models, engines (click/type/dom/nav/screenshot/network), Playwright + CDP providers, session pool, crawler/reader/ranker, security matchers. |
| `notification/` | Queue, digest, quiet hours, rate limiter, retry, routing to desktop/ntfy/telegram. |
| `identity/` | Encrypted credential vault (secrets table, macOS Keychain master key) + outbound auth strategies. **Not** API-endpoint auth. |
| `providers/mcp/` | Structured stub (`_NullMCPClient`); JSON-RPC transport deferred. |

### interfaces/
| Surface | Detail |
|---|---|
| `api/` | FastAPI `create_app()`. REST under `/api/v1` (runtime, tasks, approvals, capabilities, feedback, knowledge, memory, trajectory, attachments, trust). |
| `api/events.py` | SSE with `Last-Event-ID` resume + sequence cursors, 15s heartbeats, DB snapshot replay. |
| `api/websocket.py` | `/ws/events` firehose + `/ws/tasks/{id}/stream` with replay and keepalive. |
| `api/event_store.py` | Sequenced `task_events` log with gap detection. |
| `cli.py` | Typer, ~30 commands. |

### infra/
`Database` (aiosqlite, WAL, 13 hand-rolled migrations), `MessageBus` (durable dual-write
`event_queue` + append-only `event_log`, batch dispatch), `CronScheduler`, `ServiceRegistry`
(lifecycle ordering), circuit breaker, metrics, structlog logging, tracer, ids, clock, backup,
feedback/workflow stores, typed error hierarchy.

### frontend/ (Next.js)
Pages: dashboard, tasks (+ [task_id], live), approvals, memory, capabilities, audit, events/search.
Zod contracts in `lib/api/contracts.ts` are hand-mirrored (not generated). WebSocket/SSE clients
in `lib/events/`, `lib/websocket/`. Missing pages: skills, experiences, settings, analytics,
schedules, tools, models.

## State Ownership

| State | Owner | Durability |
|---|---|---|
| Task lifecycle | `tasks` table (SQLite) | Durable |
| Task event log | `task_events` (sequenced) | Durable |
| Cancellation | `Orchestrator._cancels` in-memory dict | **Lost on crash** |
| Execution limits | `LimitCounter` per-run object | In-memory (intentional) |
| Goal/plan during run | `GoalState`, `Plan` objects | In-memory (no checkpoints yet) |
| Memory | SQLite + Chroma | Durable |
| Trajectories/experiences | SQLite | Durable |
| Audit | SQLite, hash-chained | Durable, tamper-evident |
| Bus events | `event_queue` + `event_log` | Durable (at-least-once) |

## Extension Points

- `Verifier` protocol (goal.py) — add domain verifiers
- `ReflectionHook` protocol — add pre/post action hooks
- `Tool` protocol (tools/base.py) — new sandboxed tools
- `KnowledgeProvider` — new retrieval sources
- `Event` subclasses on the bus — new observability streams
- `bootstrap/` builders — new bounded construction modules
- `CapabilitySpec` registration — new capabilities with safety tiers
