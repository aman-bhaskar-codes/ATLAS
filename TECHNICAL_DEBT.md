# ATLAS Technical Debt Register

> Prioritized from the 2026-08-15 audit. Each item notes owner batch in the execution plan.

## P0 — Correctness / durability

| # | Debt | Location | Plan |
|---|---|---|---|
| 1 | Orchestrator writes raw SQL directly (`db.conn.execute`); no `ExecutionStore` abstraction | `orchestration/orchestrator.py` | Batch 1 |
| 2 | Cancellation state is an in-memory dict — lost on crash; cancel of unknown task silently no-ops | `orchestrator.py:67` | Batch 1 |
| 3 | `Planner._to_plan()` and `Replanner._to_plan()` are duplicated copies | `planner.py`, `replanner.py` | Batch 1 |
| 4 | `idempotency_keys` table queried by `api/idempotency.py` but not created in `_MIGRATIONS` | `infra/db.py` | Batch 1 verification |
| 5 | MessageBus batch dispatch is not crash-safe mid-batch; no DLQ for bus events (deserialize failures dropped) | `infra/bus.py` | Batch 7 |
| 6 | No execution checkpoints — long tasks cannot resume after restart | `orchestration/` | Batch 7 |

## P1 — Architecture hygiene

| # | Debt | Location | Plan |
|---|---|---|---|
| 7 | `ModelGateway.health()` reads `runtime._providers` / `runtime._health` privates | `intelligence/gateway.py:41` | Batch 3 |
| 8 | `Any`-typed event/store parameters to dodge circular imports (`set_events`, bootstrap bus) | multiple | Batch 3 |
| 9 | `app.py` still constructs capability platforms inline (~200 lines) — needs `bootstrap/capabilities.py` | `app.py:229-448` | Batch 6 |
| 10 | `Trajectory.cost_usd` always 0.0 with TODO; `model_calls` approximated by step count | `orchestrator.py:195,212` | Batch 2 |
| 11 | Trajectory `decision_traces`/`failure_records` always empty tuples with TODOs (models + store exist, capture not wired) | `orchestrator.py:184-185` | Batch 2 |
| 12 | `scripts/` directory is empty | repo root | Batch 6 |

## P2 — Missing capabilities (tracked in execution plan)

| # | Gap | Plan |
|---|---|---|
| 13 | Provider-native function calling (`tools=`/`tool_choice`); ReAct JSON parsing only | Batch 3 |
| 14 | Tool metadata (cost, latency, idempotency, side effects, rollback) | Batch 3 |
| 15 | Tool health scoring / intelligent tool routing | Batch 3 |
| 16 | Skill library, strategy memory, world state | Batch 4 |
| 17 | Experience-informed planning (planner ignores extracted experiences) | Batch 4 |
| 18 | Evaluation plane: golden tasks, evaluators, LLM judge, regression gates | Batch 2 |
| 19 | DAG parallel execution in OTAR loop (`depends_on` exists but unused) | Batch 5 |
| 20 | `ContextBudget`/`ContextRanker`/`ContextCompactor` abstractions | Batch 5 |
| 21 | Performance benchmarks with p50/p95/p99 tracking | Batch 5 |
| 22 | Frontend: 7 missing pages (skills, experiences, settings, analytics, schedules, tools, models); hand-mirrored Zod contracts | Batch 6 |
| 23 | API endpoint authn/authz (currently localhost-only by deployment) | Batch 7 |
| 24 | Tenant-aware IDs on persisted objects | Batch 7 |
| 25 | PostgreSQL storage backend; repository abstraction | Batch 8 |
| 26 | Feature flag system | Batch 6 |
| 27 | MCP provider is a stub (`_NullMCPClient`) | deferred |

## P3 — Minor

- `GoalVerifier` swallows all exceptions → passes with score 0.5 (`verifier_error`); acceptable fallback but should emit a structured warning event.
- Duplicate `retrieval.complete` debug log emitted twice in `retrieval.py`.
- `ReasoningLoop._reason_once` `stakes_tier` derives from plan risk only, not per-action risk.
- `docs/` contains only `websocket-testing-guide.md`; the new ARCHITECTURE/DEPENDENCY_GRAPH/RUNTIME_FLOW docs live at repo root alongside.
- Empty `experience_applications`-driven success_rate is computed on every apply via correlated subqueries — fine at single-user scale, revisit in Batch 8.
