# Requirements — ATLAS Phase One: Frontend Foundation & Command Center

## Background

ATLAS is a production-grade autonomous AI agent with a fully operational Python
backend: Safety Engine, Orchestrator, Memory, Intelligence Platform, and
Capability Registry are all running. The only interface today is a CLI
(`atlas run`, `atlas audit`, `atlas doctor`).

Phase One ships the **web control surface** in two parts:

1. A thin **FastAPI adapter** (`src/atlas/interfaces/api/`) that exposes the
   existing `Atlas` public object over HTTP + Server-Sent Events — no new
   business logic, no private field access.
2. A **Next.js frontend** (`frontend/`) that builds the cinematic Command
   Center shell against typed, validated contracts from that API.

The frontend renders backend state. It never invents operational data, never
duplicates safety decisions, and never shows fake success for irreversible
actions.

---

## Pre-conditions (must be true before Phase One executes)

These are blocking gaps identified during codebase analysis:

### PRE-1: `InferenceRuntime.close()` public method
`ModelGateway.close()` currently calls `self._runtime._providers.close()` via a
private attribute. Add `async def close(self) -> None` to `InferenceRuntime`
that delegates to `self._providers.close()`. Then update `ModelGateway.close()`
to call `self._runtime.close()`.

### PRE-2: Orchestrator writes task rows to the database
`Orchestrator.run()` creates a `Task` domain object but never persists it to
the `tasks` table. The `tasks` table already exists (migration 002). Add
`INSERT INTO tasks` at task creation and `UPDATE tasks SET state, updated_ts`
at completion and failure. Without this the API's `/tasks` endpoint always
returns empty.

### PRE-3: `ApprovalRequestManager` uses deprecated `get_event_loop()`
`approval.py` line: `asyncio.get_event_loop().create_future()`. Replace with
`asyncio.get_running_loop().create_future()`. This was identified in the
previous bugfix session but may not have been applied to `approval.py`.

---

## REQ-1: Backend API adapter

### REQ-1.1 Package layout
Create `src/atlas/interfaces/api/` as a proper Python package. The CLI
(`interfaces/cli.py`) must remain unchanged. The API is a separate interface
adapter — not a refactor of anything existing.

```
src/atlas/interfaces/api/
├── __init__.py
├── app.py          # FastAPI app factory, lifespan, DI
├── dependencies.py # get_atlas() dependency, shared request state
├── schemas.py      # Pydantic response/request models (API-layer only)
├── routes_runtime.py
├── routes_tasks.py
├── routes_approvals.py
├── routes_audit.py
├── routes_capabilities.py
├── events.py       # SSE broadcast from MessageBus
└── errors.py       # exception → stable HTTP error code mapping
```

### REQ-1.2 FastAPI app factory
`app.py` exports `create_app() -> FastAPI`. It uses a FastAPI `lifespan`
context manager to build the `Atlas` object once at startup (calling
`atlas.start()`), hold it for the process lifetime, and call `atlas.close()`
on shutdown. The `Atlas` instance is stored in `app.state.atlas`.

The server starts with: `uvicorn atlas.interfaces.api.app:create_app --factory`
or via the new `just serve` Justfile target.

### REQ-1.3 Dependency injection
`dependencies.py` exposes a single `get_atlas(request: Request) -> Atlas`
dependency that reads `request.app.state.atlas`. Every route receives `Atlas`
through this dependency. No route imports `build()` directly.

### REQ-1.4 Response schemas (Pydantic, versioned)
All schemas live in `schemas.py`. They are API-layer types — distinct from
internal domain types — so internal refactors cannot silently break the API
contract.

Required schemas:

**RuntimeStatusResponse**
```python
class RuntimeStatusResponse(BaseModel):
    state: Literal["starting","ready","degraded","stopping","stopped"]
    version: str          # from pyproject.toml
    environment: str      # atlas.settings.env
    kill_switch_active: bool
    active_task_count: int
    pending_approval_count: int
    last_audit_at: datetime | None
    cost_today_usd: float
```

**HealthCheckResponse + RuntimeHealthResponse**
```python
class HealthCheckResponse(BaseModel):
    name: str
    status: Literal["pass", "warn", "fail"]
    detail: str
    checked_at: datetime

class RuntimeHealthResponse(BaseModel):
    overall: Literal["healthy", "degraded", "unavailable"]
    checks: list[HealthCheckResponse]
```

**TaskResponse**
```python
class TaskResponse(BaseModel):
    id: str
    correlation_id: str
    source: str
    request: str
    state: str
    ok: bool | None
    answer: str | None
    error: str | None
    steps_taken: int
    created_at: datetime
    updated_at: datetime
```

**TaskEventResponse**
```python
class TaskEventResponse(BaseModel):
    event_id: str
    event_type: str
    schema_version: int
    ts: datetime
    task_id: str
    correlation_id: str
    state: str
    summary: str
    provider: str | None
    capability: str | None
    requires_approval: bool
```

**ApprovalResponse**
```python
class ApprovalResponse(BaseModel):
    id: str
    task_id: str | None
    correlation_id: str
    capability: str
    operation: str
    tier: int
    prompt: str
    preview: str          # exact outbound action, secrets redacted
    warnings: list[str]
    expires_at: datetime
    status: Literal["pending","approved","denied","expired"]
```

**CapabilityResponse**
```python
class CapabilityResponse(BaseModel):
    name: str
    state: Literal["ready","degraded","unavailable","planned"]
    description: str
    operations: list[str]
    providers: int
    healthy_providers: int
    requires_auth: bool
```

**AuditEventResponse**
```python
class AuditEventResponse(BaseModel):
    id: int
    correlation_id: str
    ts: datetime
    actor: str
    action: str
    tool: str | None
    tier: int | None
    decision: str | None
    outcome: str | None
    cost_tokens: int
    cost_usd: float
    # payload is NEVER returned raw — always redacted summary or null
    payload_summary: str | None
```

### REQ-1.5 Read endpoints
All endpoints are `GET`, return JSON, and never expose secrets or raw payloads.

```
GET /api/v1/runtime/status          → RuntimeStatusResponse
GET /api/v1/runtime/health          → RuntimeHealthResponse
GET /api/v1/tasks?limit=20&cursor=  → list[TaskResponse]
GET /api/v1/tasks/{task_id}         → TaskResponse
GET /api/v1/tasks/{task_id}/events  → list[TaskEventResponse]
GET /api/v1/approvals/pending       → list[ApprovalResponse]
GET /api/v1/audit?limit=50&correlation_id=  → list[AuditEventResponse]
GET /api/v1/capabilities            → list[CapabilityResponse]
GET /api/v1/providers/health        → dict (provider name → health stats)
```

### REQ-1.6 Command endpoints
All mutations require:
- `Content-Type: application/json`
- `X-Idempotency-Key` header (min 16 chars, validated)
- `X-Request-ID` header for tracing

```
POST /api/v1/tasks                          → TaskResponse (201)
POST /api/v1/tasks/{task_id}/cancel         → TaskResponse
POST /api/v1/approvals/{approval_id}/decide → ApprovalResponse
POST /api/v1/runtime/kill-switch            → {status: "tripped"}
POST /api/v1/runtime/kill-switch/reset      → {status: "reset"}
```

`POST /api/v1/tasks` body:
```json
{ "request": "...", "source": "api", "idempotency_key": "..." }
```

`POST /api/v1/approvals/{id}/decide` body:
```json
{ "decision": "approve" | "deny", "idempotency_key": "..." }
```

### REQ-1.7 Server-Sent Events stream
`GET /api/v1/events` returns an SSE stream (`text/event-stream`). The server
subscribes a handler to `atlas.bus` and pushes every `OrchestratorEvent` as a
JSON-encoded SSE event. Each event includes a unique `id` field for
deduplication.

```
event: task.created
id: <uuid>
data: {"event_id":"...","event_type":"task.created","schema_version":1,...}
```

The stream sends a `ping` comment every 15 seconds to keep the connection alive.
On disconnect the handler is removed from the bus.

### REQ-1.8 Error handling
`errors.py` maps Python exceptions to stable HTTP status codes and error bodies:

```json
{ "error": "task_not_found", "detail": "...", "request_id": "..." }
```

| Exception            | HTTP |
|----------------------|------|
| `TaskNotFound`       | 404  |
| `DeniedError`        | 403  |
| `HaltedError`        | 503  |
| `BudgetExceededError`| 402  |
| `ValidationError`    | 422  |
| Any other            | 500  |

Never leak tracebacks or internal module paths in error responses.

### REQ-1.9 CORS and security
- CORS allowed origins: `http://localhost:3000` only in dev
- `credentials: "include"` enabled only for that origin
- Kill switch and cancel endpoints require an `X-Confirm: true` header as
  a basic CSRF guard (same-site cookie or local-only deployment)
- No secrets, API keys, or credential values ever appear in any response body

### REQ-1.10 `just serve` command
Add to `Justfile`:
```
serve:
    uv run uvicorn atlas.interfaces.api.app:create_app --factory --host 127.0.0.1 --port 8730 --reload
```

Port 8730 matches the existing `ntfy_callback_base` default in `Settings`.

---

## REQ-2: Frontend scaffold

### REQ-2.1 Location and isolation
Create `frontend/` at the ATLAS project root (beside `atlas/`). The frontend
is a standalone Next.js application. It has no Python imports, no dependency
on `atlas/src`, and no access to the database directly.

### REQ-2.2 Technology stack (exact versions)
```json
{
  "next": "15.x",
  "react": "19.x",
  "typescript": "5.x",
  "@tanstack/react-query": "5.x",
  "zod": "3.x",
  "lucide-react": "latest",
  "vitest": "2.x",
  "@testing-library/react": "16.x",
  "playwright": "1.x"
}
```

No large UI component libraries (no shadcn, no MUI, no Radix as a full suite).
Primitives are hand-built from the ATLAS design system. Individual Radix
primitives (Dialog, Tooltip) are permitted only where accessibility requires
a headless primitive.

### REQ-2.3 Environment configuration
`frontend/.env.local` (gitignored):
```
NEXT_PUBLIC_ATLAS_API_URL=http://localhost:8730/api/v1
NEXT_PUBLIC_ATLAS_ENV=development
```

### REQ-2.4 Route structure
```
frontend/app/
├── layout.tsx            # AppShell, fonts, providers
├── page.tsx              # Command Center (default route)
├── loading.tsx           # Route-level skeleton
├── error.tsx             # Route-level error boundary
├── activity/page.tsx     # Live Run (Phase F2, stub in Phase One)
├── tasks/page.tsx        # Task history (Phase F3, stub)
├── approvals/page.tsx    # Approval inbox (Phase F3, stub)
├── memory/page.tsx       # Memory (Phase F3, stub)
├── capabilities/page.tsx # Capability workspaces (Phase F4, stub)
├── audit/page.tsx        # Audit timeline (Phase F3, stub)
└── settings/page.tsx     # Settings (Phase F5, stub)
```

All stub routes render an honest `<UnavailablePage name="Tasks" phase="F3" />`
component — not a blank page and not fake content.

---

## REQ-3: Design system

### REQ-3.1 OKLCH token palette
All color tokens defined in `frontend/styles/tokens.css` using OKLCH.
No raw hex/rgb in component files. The palette is exactly:

```css
--ink-950  --ink-900  --ink-850  --ink-800  --ink-700
--line
--paper-100  --paper-300  --paper-500
--royal-500  --royal-400
--gold-500   --gold-400
--jade-400   --ember-400   --danger-400
```

Plus spacing (`--space-xs` through `--space-2xl`), radius
(`--radius-sm`, `--radius-md`), and easing (`--ease-out`).

### REQ-3.2 Typography
Three typefaces, loaded via `next/font/google`:
- **Cormorant Garamond** (500, 600) — ATLAS wordmark, major titles only
- **IBM Plex Sans** (400, 500, 600) — all UI text, min 16px body
- **IBM Plex Mono** (400, 500) — IDs, providers, event payloads, code

Five-step type scale. Tabular numerals (`font-variant-numeric: tabular-nums`)
on all metric displays.

### REQ-3.3 Motion
- `120ms` button feedback
- `240ms` panel slide transitions
- `400ms` workspace-level changes
- Breathing animation on live connection dot only
- Full `prefers-reduced-motion` support — all transitions disabled at the
  CSS level, not JS

### REQ-3.4 Primitive components
All in `frontend/components/primitives/`:

| Component      | States required |
|----------------|----------------|
| `Button`       | default, hover, focus-visible, active, disabled, loading |
| `IconButton`   | same as Button |
| `Badge`        | ready, degraded, unavailable, planned, running, failed, pending |
| `Panel`        | default, elevated |
| `Skeleton`     | pulse animation, respects reduced-motion |
| `EmptyState`   | icon + heading + body + optional action |
| `ErrorState`   | heading + message + retry button |
| `UnavailablePage` | name + phase label |

---

## REQ-4: API client and contracts

### REQ-4.1 Zod contracts
`frontend/lib/api/contracts.ts` contains Zod schemas that mirror the Python
Pydantic schemas from REQ-1.4 exactly. Any field mismatch causes a visible
runtime parse error — never silent data loss.

All schemas from the Phase One doc section 5 are implemented verbatim:
`RuntimeStatusSchema`, `RuntimeHealthSchema`, `HealthCheckSchema`,
`TaskSchema`, `TaskEventSchema`, `ApprovalSchema`, `CapabilitySchema`.

In addition: `AuditEventSchema` for the audit endpoint.

### REQ-4.2 API client
`frontend/lib/api/client.ts` is the only file that calls `fetch`. All other
code calls `atlasApi.*`. The client:
- reads `NEXT_PUBLIC_ATLAS_API_URL` for the base URL
- includes `credentials: "include"`
- includes `Content-Type: application/json`
- generates a `X-Request-ID` per call
- parses responses with Zod (`.parse()`, not `.safeParse()` — fail loudly)
- throws a typed `AtlasApiError` with `status`, `code`, and `detail` fields

### REQ-4.3 TanStack Query integration
`frontend/lib/query-keys.ts` defines all query key factories. Feature modules
use `useQuery` from `@tanstack/react-query` with these keys. No raw `fetch`
inside React components.

---

## REQ-5: SSE event client

### REQ-5.1 Connection behaviour
`frontend/lib/events/socket.ts` implements `connectRuntimeEvents()` using the
browser's native `EventSource`. Behaviour:
- Reconnects on error with exponential backoff (500ms → 10s max)
- Deduplicates events by `event_id` (rolling set of 500 IDs)
- Calls `onStatus("connected" | "reconnecting" | "closed")`
- Calls `onEvent(TaskEvent)` for each valid parsed event
- Invalid/unparseable events are dropped silently (logged to console.warn)

### REQ-5.2 Reconciliation
`frontend/lib/events/reconcile.ts` exports `useRuntimeReconcile()`. After
every reconnect, and on window focus, it invalidates the TanStack Query cache
for `runtime/status`, `tasks`, and `approvals`. The SSE stream is advisory;
REST is authoritative.

---

## REQ-6: Command Center screen

### REQ-6.1 Layout
```
┌──────────────────────────────────────────────────────────────┐
│ ATLAS  [mark + Cormorant]          status strip (persistent) │
├──────────────┬───────────────────────────────────────────────┤
│  Sidebar     │  Greeting  ·  system posture                  │
│  nav items   │  ─────────────────────────────────────────    │
│              │  CommandComposer                               │
│              │  IntentReceipt (after submit)                  │
│              │  ─────────────────────────────────────────    │
│              │  ActivityTimeline  (recent episodes)          │
│              │  ─────────────────────────────────────────    │
│              │  ApprovalInbox  (if pending > 0)              │
│              │  CapabilityPosture  (status rail)             │
└──────────────┴───────────────────────────────────────────────┘
```

On mobile: bottom nav bar (5 primary items), sidebar collapses to a drawer.

### REQ-6.2 SystemStatusStrip
Persistent top bar. Shows:
```
ATLAS ONLINE  ·  local model ready  ·  2 approvals pending  ·  $0.04 today
```
or:
```
ATLAS OFFLINE  ·  reconnecting…
```

Clicking opens `HealthDrawer` (slide-in panel) showing all doctor checks
exactly as the CLI `atlas doctor` outputs them, fetched from
`GET /api/v1/runtime/health`.

### REQ-6.3 CommandComposer
Single dominant text area. Behaviour:
- Disabled and shows "Connecting…" while `runtimeStatus.state !== "ready"`
- Submits on `Enter` (not `Shift+Enter`), or on button click
- `⌘K` / `Ctrl+K` focuses it from anywhere on the page
- After submit: clears input, shows `IntentReceipt`, disables until receipt received
- `IntentReceipt` shows: task ID (truncated), source `api`, whether approval may
  be required (based on `pending_approval_count` after creation), estimated risk
- Kill switch active: shows a clear disabled state with reason

### REQ-6.4 ActivityTimeline
Reads from `GET /api/v1/tasks/{task_id}/events` for the most recent task, and
`GET /api/v1/audit?limit=20` for recent system activity. Renders a vertical
timeline with humanised event labels. No raw chain-of-thought. No raw JSON blobs
as the default view.

### REQ-6.5 ApprovalInbox
Visible only when `pending_approval_count > 0`. Shows each pending approval as
a card with:
- Capability + operation
- Tier badge
- Expiry countdown
- Exact preview (expandable)
- `[Approve once]` and `[Deny]` buttons
- Buttons disabled while the decision POST is in flight
- No optimistic update — button state changes only after server response

### REQ-6.6 CapabilityPosture
A compact status rail showing each registered capability as a single row:
`name · state badge · provider count`. Reads from `GET /api/v1/capabilities`.
Planned/unavailable capabilities show an honest `planned` badge. No interactive
controls in Phase One — that is Phase F4.

### REQ-6.7 Connected / disconnected states
Every data region implements all four states:
- **Loading** — skeleton with the same layout as the content
- **Empty** — `EmptyState` with contextual guidance (e.g. "No tasks yet — submit
  one above")
- **Error** — `ErrorState` with retry button that re-runs the query
- **Disconnected** — distinct from error; shows reconnection countdown

---

## REQ-7: Accessibility baseline

- WCAG AA contrast on all text/background combinations in the OKLCH palette
- Keyboard-only navigation: Tab, Enter, Escape, arrow keys work everywhere
- `:focus-visible` rings on all interactive elements (never suppressed globally)
- `aria-live="polite"` on the status strip and approval count
- Semantic HTML: `<nav>`, `<main>`, `<aside>`, `<header>` landmarks
- No color-only status meaning (every status badge has a text label)
- Minimum 44×44px touch targets on all interactive elements
- `<dialog>` for blocking confirmations only; drawers use `role="dialog"` with
  `aria-modal`

---

## REQ-8: Testing

### REQ-8.1 Contract tests
`frontend/tests/contracts/` — Vitest tests that parse every fixture JSON
from `frontend/tests/fixtures/` through the Zod schemas. Any schema drift
from the backend causes an explicit test failure.

### REQ-8.2 Component tests
Vitest + Testing Library tests for:
- `CommandComposer` — empty, loading, submit, success, error
- `SystemStatusStrip` — online, offline, reconnecting, kill switch active
- `ApprovalInbox` — pending, approve, deny, expired
- `CapabilityPosture` — ready, degraded, planned
- `ActivityTimeline` — empty, loading, events list

### REQ-8.3 E2E tests (Playwright)
`frontend/tests/e2e/` — tests against a fake API server (MSW):
- Open with backend unavailable → shows disconnected state
- Connect and see runtime status
- Submit task → receive receipt
- Approve pending action
- Deny pending action
- Kill switch visible and requires confirmation

---

## Non-goals for Phase One

- Browser automation workspace (Phase F4)
- Research workspace (Phase F5)
- Memory edit controls (Phase F3)
- Full audit timeline with filters (Phase F3)
- Calendar/Contacts/Email workspaces (Phase F4)
- Telegram bridge UI (later in Phase F2/F3)
- Multi-user auth, teams, billing
- Any feature that requires a backend capability not yet implemented
