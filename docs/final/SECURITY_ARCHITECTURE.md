# SECURITY_ARCHITECTURE — ATLAS (verified)

Threat model: a single local user running an autonomous agent that can call tools. The
core guarantee is that **no tool runs except through the safety funnel**, and that
**secrets never reach the UI, logs, events, or trajectories**.

## Reference monitor

`SafetyEngine.guard()` is the sole path to execution (see FINAL_RUNTIME_FLOW). Order is
fixed and non-bypassable: kill-switch → classify (tier 0–4) → policy chain → **audit
first** → decision. **The LLM proposes; ATLAS decides** — a model can request an action
but cannot execute one.

### Risk tiers
- **T0 AUTO** — read-only/no side effects.
- **T1 NOTIFY** — reversible writes; proceed + notify.
- **T2 CONFIRM** — irreversible; requires human approval.
- **T3 DANGEROUS** — approval **plus a one-time code**, then a second kill-switch check.
- **T4 BLOCK** — refused outright.

### Hard blocks (no approval can lift)
Credential/secret access, financial transactions, mass deletion (>25 items), and any edit
to the safety configuration itself.

## Kill switch

Global halt. **There is no HTTP endpoint to trip it** (verified — no route exists); it is
controlled via CLI/file only, by design, so a compromised browser session cannot arm or
disarm it. Its state is exposed **read-only** to the UI via
`runtime/status.kill_switch_active`. Consequence for the frontend: the kill-switch control
must be a truthful **indicator**, not a fake POST button (see TECHNICAL_DEBT_FINAL).

## Audit chain

Append-only, **SHA-256 hash-chained** log; each entry is written *before* the decision it
records. Integrity is externally checkable via `GET /api/v1/audit/verify`. Chain-of-thought
is never persisted — only structured step summaries.

## Secret handling (verified)

- **Event store filtering:** `TaskEventStore` builds `safe_metadata` by **dropping**
  sensitive keys (`args`, `tool`, `error`, `risk`, `confidence`, …) before persistence, so
  raw tool arguments never land in `task_events` or the SSE stream.
- **No secret fields in contracts:** `frontend/lib/api/contracts.ts` carries no API-key /
  token / credential field — the UI has no channel to receive one.
- Standing rules honored: never expose keys to frontend/logs/events/trajectories/source;
  do not record secrets; do not expose the internal fact table unless requested.

## Transport / access

- **CORS** locked to `http://localhost:3000`.
- **API keys** optional; local-open on loopback by default (documented, not silent).
- **Rate limiting** at the edge (token bucket) blunts runaway/abusive call loops.
- **Sandboxed execution** for tool calls (Docker sandbox in `safety/`).

## Security gaps (🛠 — logged)

- CORS `allow_methods=["GET","POST"]` is *too narrow* (breaks automation PUT/DELETE) — a
  correctness bug, but note: widening methods must stay scoped to the localhost origin.
- Local-open mode means anything on loopback can drive the API; documented, acceptable for
  single-user local use, but should be called out in the OPERATIONS_GUIDE.
