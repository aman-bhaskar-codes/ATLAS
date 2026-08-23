# API_SURFACE — ATLAS (verified)

Backend: FastAPI factory `interfaces/api/app.py::create_app()`, `uvicorn --factory`,
**:8730**. OpenAPI/Swagger at `/api/docs`. Inventory below is the client contract
(`frontend/lib/api/client.ts`) confirmed against the mounted routers in `app.py`.

## Mounting scheme (important)

`app.py` mounts two ways. **Get the prefix right or you 404:**

- `prefix="/api/v1"` (router paths are relative): health, runtime, tasks, approvals,
  capabilities, feedback, attachments, events (SSE), learning, ops.
- `prefix=""` (router is **self-prefixed**, already contains `/api/v1/...`): knowledge,
  memory, trajectory, trust, events_ws (WebSocket), providers, automations.

Net effect: every REST path is under `/api/v1`. The `API_BASE` default already ends in
`/api/v1`, so client paths must be relative (`/runtime/status`), **not** `/api/v1/...`.

## Endpoints in use

### atlasApi (typed, zod-validated)
| Method | Path | Purpose |
|---|---|---|
| GET | `/runtime/status` | ATLAS STATUS source (state, counts, kill_switch, version) |
| GET | `/runtime/health` | overall + per-check health |
| GET | `/tasks?limit=` | task list `{items}` |
| GET | `/tasks/{id}` | task snapshot |
| GET | `/tasks/{id}/events?after_sequence=` | event page (baseline/replay) |
| GET | `/tasks/{id}/events/stream` | **SSE** live trace |
| POST | `/tasks` | create `{request, idempotency_key, source, attachments}` |
| POST | `/tasks/{id}/cancel` | cooperative cancel `{idempotency_key, reason}` |
| GET | `/approvals/pending` | pending approvals |
| POST | `/approvals/{id}/decide` | `{decision: approve\|deny, idempotency_key}` |
| GET | `/capabilities` | capability posture |

### trustApi (untyped `requestJSON`)
`/tasks`, `/tasks/{id}`, `/approvals/pending`, `/approvals/{id}`,
`/approvals/{id}/decide`, `/memory/search`, `/memory/facts/{id}`,
`/memory/facts/{id}/correct`, `/audit` (+ `/audit/verify` for chain integrity).

### autonomyApi (untyped) — `/automations`
`GET /automations`, `GET /automations/{id}`, **`POST`**, **`PUT /automations/{id}`**,
**`DELETE /automations/{id}`**. ⚠️ CORS `allow_methods=["GET","POST"]` blocks PUT/DELETE
from the browser — see TECHNICAL_DEBT_FINAL.

### learningApi (untyped) — `/learning/*`
`skills`, `skills/{id}/disable`, `strategies`, `world`, `evaluations`, `analytics`.

### opsApi (untyped) — `/ops/*`
`tools`, `models`, `providers`, `schedules`, `schedules/{id}/toggle`.

### providersApi (untyped) — `/providers/*`
`health`, `free`, `profile`, `quota`, `capability-matrix`.

### trajectoryApi (untyped) — `/trajectory/*`
`experiences`. ⚠️ **BUG:** client calls `requestJSON('/api/v1/trajectory/experiences')`
while `API_BASE` already ends `/api/v1` → double prefix → 404. Fix: drop the prefix.

## Cross-cutting

- **Auth:** API keys optional; local-open mode by default (no key required on loopback).
  No key material appears in any request/response contract.
- **Rate limit:** `TokenBucketLimiter(capacity=120, refill_per_minute=60)`.
- **CORS:** `allow_origins=["http://localhost:3000"]`, `allow_methods=["GET","POST"]`
  (⚠️ too narrow for the PUT/DELETE automation routes).
- **Client timeout:** 8s (`AbortController` in `fetchWithTimeout`).
