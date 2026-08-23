# FINAL_RUNTIME_FLOW — ATLAS (verified)

## Task lifecycle

Task states (authoritative — from `frontend/lib/api/contracts.ts::TASK_STATES`, mirrors
backend): `created → ready → building_context → planning → reasoning → waiting_tool →
executing → observing → (completed | failed | cancelled)`. Terminal:
`completed/failed/cancelled`.

A task is created via `POST /api/v1/tasks` (`{request, idempotency_key, source}`) and
picked up by the orchestrator. Every step emits an `OrchestratorEvent` on the bus.

## OTAR reasoning loop

**Observe → Think (model call) → Act (tool, via Safety) → Reflect**, then **Verify**
against success criteria. On pass → answer + full trajectory. On fail with budget left →
bounded **Replan** back to Observe. The loop is bounded by construction (step / token /
wall-clock limits raise typed errors — never an infinite loop).

## Safety funnel (reference monitor)

No tool executes except through `SafetyEngine.guard()`. Fixed path:

1. **Kill switch** — if active, halt. (No HTTP trip endpoint — CLI/file only; exposed
   read-only via `runtime/status.kill_switch_active`.)
2. **Classify** into risk tier 0–4.
3. **Policy chain** evaluation.
4. **Audit** — append to SHA-256 hash-chained, append-only log *before* the decision
   (verifiable via `GET /api/v1/audit/verify`).
5. **Decision** — deny/BLOCK · require human approval (+ one-time code if DANGEROUS,
   then kill-switch re-check) · auto-approve → execute in sandbox.

Tiers: **T0 AUTO** (read-only) · **T1 NOTIFY** (reversible) · **T2 CONFIRM**
(irreversible) · **T3 DANGEROUS** (approval + one-time code) · **T4 BLOCK**. Hard-blocked
regardless of approval: credential access, financial transactions, mass deletion
(>25 items), edits to the safety config. Principle: **the LLM proposes; ATLAS decides.**

## Event → UI path

`OrchestratorEvent` → bus. In the API lifespan (`app.py`) two subscribers fire:
(1) `TaskEventStore.record(...)` persists to `task_events` with a monotonic `sequence`
and **filters sensitive keys** out of `safe_metadata` (drops `args`, `tool`, `error`,
`risk`, `confidence`, …); (2) a per-task fan-out signals SSE queues. Clients stream via
`GET /api/v1/tasks/{id}/events/stream` (named events `task_event`/`heartbeat`/
`stream_closed`); a parallel WebSocket broadcaster serves global/memory event feeds.

The frontend `features/runtime-console/useTaskEvents.ts` consumes SSE with sequence
reconciliation, gap detection + resync, exponential backoff, and visibility-based
reconnect — and polls the task snapshot (`GET /tasks/{id}`) every 2s until terminal as
the authoritative baseline.

## Persistence & recovery

Tasks/events/audit in SQLite; vectors in Chroma. On startup `recover_interrupted_tasks()`
reconciles tasks left mid-flight by a crash (Batch 7). Raw chain-of-thought is **not**
persisted or exposed — only structured step summaries.
