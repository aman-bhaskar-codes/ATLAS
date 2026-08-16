# ATLAS Technical Debt Register

> Prioritized from the 2026-08-15 audit; updated 2026-08-16 after Batches 1-8.
> CLOSED items keep their row for history with a ✅ marker.

## P0 — Correctness / durability

| # | Debt | Location | Plan |
|---|---|---|---|
| 1 | ✅ CLOSED (Batch 1): ExecutionStore/CancellationStore protocols + SQLite implementations |
| 2 | ✅ CLOSED (Batch 1/7): durable cancellation + fail-clean crash recovery |
| 3 | ✅ CLOSED (Batch 1): shared `plan_parsing.py` |
| 4 | `idempotency_keys` table queried by `api/idempotency.py` but not created in `_MIGRATIONS` | `infra/db.py` | Batch 1 verification |
| 5 | MessageBus batch dispatch is not crash-safe mid-batch; no DLQ for bus events (deserialize failures dropped) | `infra/bus.py` | Batch 7 |
| 6 | ✅ CLOSED (Batch 7): checkpoints saved per-step; fail-clean recovery (auto-resume pending idempotency keys — see below) |

## P1 — Architecture hygiene

| # | Debt | Location | Plan |
|---|---|---|---|
| 7 | `ModelGateway.health()` reads `runtime._providers` / `runtime._health` privates | `intelligence/gateway.py:41` | Batch 3 |
| 8 | `Any`-typed event/store parameters to dodge circular imports (`set_events`, bootstrap bus) | multiple | Batch 3 |
| 9 | `app.py` still constructs capability platforms inline (~200 lines) — needs `bootstrap/capabilities.py` | `app.py:229-448` | Batch 6 |
| 10 | `Trajectory.cost_usd` still 0.0 (LLMCallTracker has the data; wire-through pending) | — |
| 11 | Trajectory `decision_traces`/`failure_records` always empty tuples with TODOs (models + store exist, capture not wired) | `orchestrator.py:184-185` | Batch 2 |
| 12 | `scripts/` directory is empty | repo root | Batch 6 |

## P2 — Missing capabilities (tracked in execution plan)

| # | Gap | Plan |
|---|---|---|
| 13 | ✅ CLOSED (Batch 3): native calling threaded end-to-end; ReAct remains the default runtime path — switching the ReasoningLoop to native-first needs eval-gated rollout |
| 14 | ✅ CLOSED (Batch 3) |
| 15 | ✅ CLOSED (Batch 3) |
| 16 | ✅ CLOSED (Batch 4) |
| 17 | ✅ CLOSED (Batch 4) |
| 18 | ✅ CLOSED (Batch 2) |
| 19 | ✅ CLOSED (Batch 5) |
| 20 | ✅ CLOSED (Batch 5) |
| 21 | ✅ CLOSED (Batch 5): tests/performance + benchmarks/run.py |
| 22 | ✅ Pages CLOSED (Batch 6); generated TS types still open |
| 23 | ✅ CLOSED (Batch 7): bearer keys + readonly role + rate limits (Batch 8) |
| 24 | ✅ Seed CLOSED (Batch 7): tenant_id on tasks/trajectories/checkpoints; full tenancy needs identity (SCALE_PATH.md) |
| 25 | Deferred by design (SCALE_PATH.md): protocol seams exist; extraction on measured need |
| 26 | OPEN: feature flags still missing (models.yaml `enabled` + critique.enabled cover partial cases) |
| 27 | MCP provider is a stub (`_NullMCPClient`) | deferred |

## P3 — Minor

- `GoalVerifier` swallows all exceptions → passes with score 0.5 (`verifier_error`); acceptable fallback but should emit a structured warning event.
- Duplicate `retrieval.complete` debug log emitted twice in `retrieval.py`.
- `ReasoningLoop._reason_once` `stakes_tier` derives from plan risk only, not per-action risk.
- `docs/` contains only `websocket-testing-guide.md`; the new ARCHITECTURE/DEPENDENCY_GRAPH/RUNTIME_FLOW docs live at repo root alongside.
- Empty `experience_applications`-driven success_rate is computed on every apply via correlated subqueries — fine at single-user scale, revisit in Batch 8.
