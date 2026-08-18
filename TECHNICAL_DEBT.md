# ATLAS Technical Debt Register

> Prioritized from the 2026-08-15 audit; updated 2026-08-16 after Batches 1-8.
> CLOSED items keep their row for history with a ✅ marker.

## P0 — Correctness / durability

| # | Debt | Location | Plan |
|---|---|---|---|
| 1 | ✅ CLOSED (Batch 1): ExecutionStore/CancellationStore protocols + SQLite implementations |
| 2 | ✅ CLOSED (Batch 1/7): durable cancellation + fail-clean crash recovery |
| 3 | ✅ CLOSED (Batch 1): shared `plan_parsing.py` |
| 4 | ✅ CLOSED (Batch 9.1): `idempotency_keys` table migration added to migration 007 + comprehensive test suite | `infra/db.py`, `tests/interfaces/test_idempotency.py` | COMPLETE |
| 5 | MessageBus batch dispatch is not crash-safe mid-batch; no DLQ for bus events (deserialize failures dropped) | `infra/bus.py` | Batch 7 |
| 6 | ✅ CLOSED (Batch 7): checkpoints saved per-step; fail-clean recovery (auto-resume pending idempotency keys — see below) |
| 6.1 | ✅ CLOSED (Batch 9.2): Provider lifecycle events calling `.emit()` instead of `.publish()` with typed Event | `intelligence/runtime/inference.py`, `intelligence/runtime/fallback.py` | COMPLETE |
| 6.2 | ✅ CLOSED (Batch 9.3): Two incompatible `QuotaExhaustedError` classes consolidated into one with proper constructor | `intelligence/errors.py`, `intelligence/governance/quota_governor.py` | COMPLETE |

## P1 — Architecture hygiene

| # | Debt | Location | Plan |
|---|---|---|---|
| 7 | `ModelGateway.health()` reads `runtime._providers` / `runtime._health` privates | `intelligence/gateway.py:41` | Batch 3 |
| 8 | `Any`-typed event/store parameters to dodge circular imports (`set_events`, bootstrap bus) | multiple | Batch 3 |
| 9 | `app.py` still constructs capability platforms inline (~200 lines) — needs `bootstrap/capabilities.py` | `app.py:229-448` | Batch 6 |
| 10 | ✅ CLOSED (Batch 10.3): `Trajectory.cost_usd` wired from `LLMCallTracker.cost_by_task()` | `orchestrator.py:285` | COMPLETE |
| 11 | Trajectory `decision_traces`/`failure_records` always empty tuples (Phase 3+ work; models+store+APIs exist, requires instrumenting replanner/router/reasoning/error-handlers) | `orchestrator.py:286-287` | Phase 3+ |
| 12 | ✅ CLOSED (Batch 10.6): `scripts/eval_gate.py` added - regression gate for golden suite scoring | `scripts/eval_gate.py` | COMPLETE |

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
| 26 | ✅ CLOSED (Batch 11.1): Feature flags deferred until concrete need emerges. Current state sufficient: config-level `enabled` booleans (tracing/critique/browser), per-model `enabled` flags (models.yaml), environment-driven toggles (allow_cloud). Unified feature flag system (percentage rollouts, A/B testing, user-based targeting) to be designed when multi-tenant or gradual rollout needs arise. | `config/settings.yaml`, `config/models.yaml` | COMPLETE |
| 27 | ✅ CONFIRMED (Batch 11.2): MCP provider `_NullMCPClient` is intentional architectural placeholder (deferred to Part 6.9). Stub keeps MCPProvider importable/testable and defines client surface contract (open/ping/call_tool/close) for future stdio/HTTP implementation. By design, not blocking current functionality. | `capabilities/providers/mcp/base.py:79-92` | DEFERRED (Part 6.9) |

## P3 — Minor

- ✅ CLOSED (Batch 12): `GoalVerifier` now emits structured warning event (`goal.verification_error`) when verification fails, includes error repr and detail. Fallback behavior unchanged (passed=True, score=0.5). | `orchestration/goal.py:224-229` | COMPLETE |
- ✅ CLOSED (Batch 10.5): Duplicate `retrieval.complete` debug log removed from `retrieval.py` (kept the one with latency_ms).
- `ReasoningLoop._reason_once` `stakes_tier` derives from plan risk only, not per-action risk. (Future enhancement: per-action risk scoring)
- ✅ CLOSED (Batch 12): `docs/` now contains autonomy/, free-first/, autonomy_fabric.md, and websocket-testing-guide.md. Architecture docs (ARCHITECTURE/DEPENDENCY_GRAPH/RUNTIME_FLOW) remain at repo root by design. | `docs/` | COMPLETE |
- Empty `experience_applications`-driven success_rate is computed on every apply via correlated subqueries — fine at single-user scale, revisit in Batch 8. (Performance note: acceptable for current use)
