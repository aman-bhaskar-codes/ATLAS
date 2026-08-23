# PERFORMANCE_ARCHITECTURE — ATLAS (verified)

Single-user, local-first. "Performance" here means responsiveness + bounded resource use,
not horizontal scale. Claims below are from code actually read; open gaps are marked 🛠.

## Live updates: SSE + reconciliation

The runtime console does not poll for the event stream — it uses SSE
(`GET /api/v1/tasks/{id}/events/stream`, named events `task_event`/`heartbeat`/
`stream_closed`). `features/runtime-console/useTaskEvents.ts` adds:
- **Monotonic sequence tracking** — each event carries `sequence`; the client detects gaps.
- **Gap recovery** — on a gap it replays via `GET /tasks/{id}/events?after_sequence=N`.
- **Exponential backoff** on reconnect; **visibility-based reconnect** (resumes on tab focus).
- **Snapshot baseline** — `GET /tasks/{id}` polled every 2s until terminal as the source
  of truth the stream is reconciled against.

This means a dropped connection or a missed event self-heals without a full reload.

## Polling cadences (react-query)

Where push isn't used, intervals are deliberately conservative to limit load:
task snapshot ~2s (until terminal), tasks list / approvals ~5s, health ~15s. All requests
carry an **8s abort timeout** so a hung backend can't wedge the UI.

## Backpressure / limits

- **Rate limiter:** `TokenBucketLimiter(capacity=120, refill_per_minute=60)` at the API edge.
- **Reasoning bounds:** OTAR loop is bounded by step/token/wall-clock budgets (typed
  errors on exhaustion) — no unbounded model spend per task.
- **Startup backup** runs async in the lifespan so it doesn't block first request.
- **WebSocket broadcaster** fans out global/memory events to many listeners from one bus
  subscription (no per-client polling of those feeds).

## Data layer

SQLite via `aiosqlite` (async, non-blocking); ChromaDB for vectors. Event persistence is
append-only with an indexed `sequence` per task, so replay-after-gap is a range scan.

## Known performance gaps (🛠 — logged, not fixed this pass)

- No list **virtualization** — long task/event/audit lists render every row.
- No charting library — metrics are numeric/text (no historical graphs).
- Some views (tasks list, approvals) are **poll-based**, not pushed.
- No explicit bundle-size budget / route-level code-split audit for the frontend yet.
- No load/soak test in CI (single-user assumption not stress-verified).
