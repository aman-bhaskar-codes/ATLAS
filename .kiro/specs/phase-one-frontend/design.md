# Design — ATLAS Phase One: Frontend Foundation & Command Center

## Overview

Phase One is split into two layers that build in strict sequence:

1. **Backend pre-conditions** (3 surgical Python fixes) — unblock the API
2. **Backend API adapter** (`src/atlas/interfaces/api/`) — thin FastAPI layer over the existing `Atlas` object
3. **Frontend** (`frontend/`) — Next.js 15 app consuming that API

No layer invents data. No layer reaches through the one below it.

---

## Layer 1: Backend Pre-conditions

### Fix A — `InferenceRuntime.close()`

**File:** `src/atlas/intelligence/runtime/inference.py`

Add one method at the bottom of the class:

```python
async def close(self) -> None:
    await self._providers.close()
```

**File:** `src/atlas/intelligence/gateway.py`

Change `close()` from:
```python
async def close(self) -> None:
    await self._runtime._providers.close()   # private access — wrong
```
to:
```python
async def close(self) -> None:
    await self._runtime.close()              # public — correct
```

No other changes. One method added, one line changed.


### Fix B — Orchestrator writes task rows

**File:** `src/atlas/orchestration/orchestrator.py`

The `Orchestrator.__init__` already receives `ids: IdGenerator` and `clock: Clock`.
It needs the `Database` injected so it can persist task rows.

**Step 1:** Add `db: Database` to `Orchestrator.__init__` parameters and store as `self._db`.

**Step 2:** In `run()`, after the `Task` domain object is created and before the first
`self._events.emit(...)` call, insert:

```python
await self._db.conn.execute(
    "INSERT OR IGNORE INTO tasks(id, source, state, payload, "
    "idempotency_key, created_ts, updated_ts) VALUES (?,?,?,?,?,?,?)",
    (task.id, task.source, "created",
     json.dumps({"request": task.request, "correlation_id": task.correlation_id}),
     None,
     task.created_ts.isoformat(), task.created_ts.isoformat()),
)
await self._db.conn.commit()
```

**Step 3:** At the end of `run()`, in the `finally` block (after `self._cancels.pop`),
add an update that writes the final state back:

```python
await self._db.conn.execute(
    "UPDATE tasks SET state=?, updated_ts=? WHERE id=?",
    (machine.state.value, self._clock.now().isoformat(), task.id),
)
await self._db.conn.commit()
```

**Step 4:** In `app.py`, pass `db=db` to the `Orchestrator(...)` constructor call.

Add `import json` at the top of `orchestrator.py` (it is not currently imported there).


### Fix C — `ApprovalRequestManager` deprecated `get_event_loop()`

**File:** `src/atlas/capabilities/notification/approval.py`

Line inside `request()` method:
```python
# Before:
fut: asyncio.Future[bool] = asyncio.get_event_loop().create_future()
# After:
fut: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
```

One word change. Same fix as was applied to `notify.py` in the previous session.

---

## Layer 2: Backend API Adapter

### Package structure

```
src/atlas/interfaces/api/
├── __init__.py          # empty, marks package
├── app.py               # FastAPI factory + lifespan
├── dependencies.py      # get_atlas() DI provider
├── schemas.py           # Pydantic response/request models
├── routes_runtime.py    # GET /runtime/status, /runtime/health, kill-switch
├── routes_tasks.py      # GET/POST /tasks, GET /tasks/{id}, /tasks/{id}/events
├── routes_approvals.py  # GET /approvals/pending, POST /approvals/{id}/decide
├── routes_audit.py      # GET /audit
├── routes_capabilities.py # GET /capabilities, GET /providers/health
├── events.py            # SSE broadcast from MessageBus
└── errors.py            # exception → HTTP error mapping
```

### `app.py` — factory + lifespan

```python
from contextlib import asynccontextmanager
from importlib.metadata import version
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from atlas.app import build

@asynccontextmanager
async def lifespan(app: FastAPI):
    atlas = await build()
    await atlas.start()
    app.state.atlas = atlas
    app.state.version = version("atlas")
    yield
    await atlas.close()

def create_app() -> FastAPI:
    app = FastAPI(title="ATLAS API", version="1", lifespan=lifespan)
    app.add_middleware(CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    # register error handlers from errors.py
    # include routers from each routes_*.py
    return app
```

The `create_app` pattern (factory, not module-level instance) allows uvicorn to
call `--factory` and tests to call `create_app()` with a fresh state each time.


### `dependencies.py`

```python
from fastapi import Request
from atlas.app import Atlas

def get_atlas(request: Request) -> Atlas:
    return request.app.state.atlas
```

Every route receives `Atlas` via `Depends(get_atlas)`. No route imports `build()`
or accesses `app.state` directly.

### `schemas.py` — design decisions

All schema classes inherit from `pydantic.BaseModel`. Field names use
**snake_case** (matching Python convention and the existing backend types).
The TypeScript Zod schemas use the same names with no transformation.

Key design decisions:
- `TaskResponse.state` mirrors `TaskState` StrEnum values exactly
  (`"created"`, `"planning"`, `"completed"`, `"failed"`, etc.)
- `ApprovalResponse.preview` is the exact outbound action text already
  generated by `EmailPlatform._render_preview()` or `CalendarPlatform._render_preview()` —
  not a summary, not a paraphrase
- `AuditEventResponse` never includes `payload` body — only a
  `payload_summary: str | None` that contains the redacted key names only
  (e.g. `"args.path, args.operation"`) so the UI can show what was logged
  without exposing content
- `CapabilityResponse.state` is computed from `CapabilityHealth.is_available()`
  and `CapabilityRegistry.get()` — not stored anywhere, derived on every request

### `routes_runtime.py` — design

`GET /api/v1/runtime/status` assembles `RuntimeStatusResponse` from:
- `atlas.killswitch.is_active()` → `kill_switch_active`
- `atlas.cap_registry.all()` + in-memory task counter → `active_task_count`
  (maintained as a simple `asyncio.Lock`-protected int on `app.state`)
- `atlas.audit.cost_today()` → `cost_today_usd`
- `atlas.audit.tail(1)` → `last_audit_at` (first row's `ts` field)
- `len(atlas.notification_platform._approvals._pending)` → `pending_approval_count`

`GET /api/v1/runtime/health` calls `run_doctor(atlas)` from the existing
`diagnostics/doctor.py` and maps `CheckResult` to `HealthCheckResponse`.
The `overall` field is `"healthy"` if all checks pass, `"degraded"` if any
are `"warn"`, `"unavailable"` if any are `"fail"`.

`POST /api/v1/runtime/kill-switch` calls `atlas.killswitch.trip()`.
`POST /api/v1/runtime/kill-switch/reset` calls `atlas.killswitch.reset()`.
Both require `X-Confirm: true` header. Both return `{"status": "tripped"|"reset"}`.


### `routes_tasks.py` — design

`GET /api/v1/tasks` queries the `tasks` table directly via `atlas.db.conn`:
```sql
SELECT id, source, state, payload, created_ts, updated_ts
FROM tasks ORDER BY created_ts DESC LIMIT ? OFFSET ?
```
The `request` field is stored in the JSON `payload` column. Parse it with
`json.loads(row["payload"])["request"]`.

`GET /api/v1/tasks/{task_id}/events` queries the `episodes` table:
```sql
SELECT * FROM episodes
WHERE correlation_id = (SELECT json_extract(payload,'$.correlation_id')
                        FROM tasks WHERE id = ?)
ORDER BY step ASC, id ASC
```
Each episode row is mapped to `TaskEventResponse` with:
- `event_id` = `str(row["id"])`
- `event_type` = `row["kind"]` (episode kind string)
- `summary` = humanised label (see humanise function below)
- `state` = `row["outcome"] or ""`
- `requires_approval` = `row["kind"] == "action" and "approval" in (row["content"] or "")`

Humanise function maps episode kinds to readable labels:
```python
def _humanise(kind: str, role: str | None, content: str) -> str:
    if kind == "action":   return f"Action: {content[:80]}"
    if kind == "observation" and role == "system": return f"Result: {content[:80]}"
    if kind == "message" and role == "agent":      return "Reasoning step"
    return content[:80]
```

`POST /api/v1/tasks` body is `{"request": str, "source": "api", "idempotency_key": str}`.
It builds an `InboundEvent` and calls `atlas.orchestrator.run(event)` in a
background asyncio task (so the HTTP response returns immediately with the
task receipt). The active_task_count on `app.state` is incremented/decremented
around the background task.

`POST /api/v1/tasks/{task_id}/cancel` calls `atlas.orchestrator.cancel(task_id)`.

### `routes_approvals.py` — design

`GET /api/v1/approvals/pending` reads `atlas.notification_platform._approvals._pending`
(the in-memory dict of pending futures). For each pending `ApprovalRequest`,
it returns an `ApprovalResponse`. Note: `_pending` stores futures keyed by
request ID — the `ApprovalRequest` objects themselves must be stored alongside
them. This requires a small change to `ApprovalRequestManager`: add
`self._requests: dict[str, ApprovalRequest] = {}` that stores the request on
entry and removes it on resolution (same lifecycle as `_pending`).

`POST /api/v1/approvals/{approval_id}/decide` calls
`atlas.notification_platform._approvals.resolve(approval_id, approved=True|False)`.
Returns the updated `ApprovalResponse` with `status: "approved"|"denied"`.

### `routes_audit.py` — design

`GET /api/v1/audit` calls `atlas.audit.tail(limit)` optionally filtered by
`correlation_id`. Maps each row to `AuditEventResponse`. The `payload_summary`
is constructed by fetching the payload row from the `payloads` table and
returning only the key names: `", ".join(json.loads(body).keys())`.

### `routes_capabilities.py` — design

`GET /api/v1/capabilities` iterates `atlas.cap_registry.all()` and for each
`CapabilitySpec`:
- gets provider count from `atlas.cap_providers.all_providers()` filtered by capability
- computes healthy count from `atlas.cap_health.is_available(p.name)` for each
- determines state: `"ready"` if healthy_providers > 0, `"degraded"` if some
  unhealthy, `"unavailable"` if none healthy, `"planned"` if no providers registered

`GET /api/v1/providers/health` returns `atlas.cap_health.snapshot()` merged with
`atlas.gateway.health()` (model provider health from `HealthMonitor`).


### `events.py` — SSE broadcast design

```python
from asyncio import Queue
from fastapi import Request
from fastapi.responses import StreamingResponse
from atlas.infra.bus import Event

async def runtime_event_stream(request: Request, atlas) -> StreamingResponse:
    queue: Queue[str] = Queue(maxsize=200)

    async def handler(event: Event) -> None:
        if queue.full():
            queue.get_nowait()   # drop oldest to prevent memory growth
        data = event.model_dump_json()
        await queue.put(f"id: {id(event)}\ndata: {data}\n\n")

    atlas.bus.subscribe("orchestrator", handler)

    async def generate():
        yield ": ping\n\n"    # initial keep-alive
        ping_count = 0
        while not await request.is_disconnected():
            try:
                chunk = await asyncio.wait_for(queue.get(), timeout=15.0)
                yield chunk
            except asyncio.TimeoutError:
                ping_count += 1
                yield f": ping {ping_count}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})
```

The handler is removed when the generator exits (on disconnect). The `maxsize=200`
cap prevents unbounded growth if the client is slow.

### `errors.py` — exception mapping

```python
from fastapi import Request
from fastapi.responses import JSONResponse

async def atlas_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    from atlas.safety.engine import DeniedError, HaltedError
    from atlas.intelligence.errors import BudgetExceededError
    mapping = {
        DeniedError: (403, "denied"),
        HaltedError: (503, "halted"),
        BudgetExceededError: (402, "budget_exceeded"),
        KeyError: (404, "not_found"),
    }
    for exc_type, (status, code) in mapping.items():
        if isinstance(exc, exc_type):
            return JSONResponse(status_code=status,
                content={"error": code, "detail": str(exc)[:200],
                         "request_id": request.headers.get("X-Request-ID")})
    return JSONResponse(status_code=500,
        content={"error": "internal_error", "detail": "unexpected server error",
                 "request_id": request.headers.get("X-Request-ID")})
```

Never include tracebacks. Never include internal module paths.

### New dependencies for the API

Add to `pyproject.toml` dependencies:
```toml
"fastapi>=0.115",
"uvicorn[standard]>=0.30",
```

Add to `Justfile`:
```
serve:
    uv run uvicorn atlas.interfaces.api.app:create_app --factory --host 127.0.0.1 --port 8730 --reload
```

---

## Layer 3: Frontend Architecture

### Directory tree (complete Phase One)

```
frontend/
├── app/
│   ├── layout.tsx            # root layout: fonts, QueryClient, AppShell
│   ├── page.tsx              # Command Center
│   ├── loading.tsx           # route skeleton
│   ├── error.tsx             # route error boundary
│   ├── globals.css           # resets + token import
│   ├── activity/page.tsx     # stub: UnavailablePage
│   ├── tasks/page.tsx        # stub: UnavailablePage
│   ├── approvals/page.tsx    # stub: UnavailablePage
│   ├── memory/page.tsx       # stub: UnavailablePage
│   ├── capabilities/page.tsx # stub: UnavailablePage
│   ├── audit/page.tsx        # stub: UnavailablePage
│   └── settings/page.tsx     # stub: UnavailablePage
├── components/
│   ├── shell/
│   │   ├── AppShell.tsx
│   │   ├── Sidebar.tsx
│   │   ├── MobileNav.tsx
│   │   └── SystemStatusStrip.tsx
│   ├── command/
│   │   ├── CommandComposer.tsx
│   │   ├── IntentReceipt.tsx
│   │   └── HealthDrawer.tsx
│   ├── runtime/
│   │   ├── ActivityTimeline.tsx
│   │   ├── TaskStateBadge.tsx
│   │   └── EventConnectionBadge.tsx
│   ├── approvals/
│   │   ├── ApprovalInbox.tsx
│   │   ├── ApprovalCard.tsx
│   │   └── ApprovalDecisionButtons.tsx
│   ├── capabilities/
│   │   └── CapabilityPosture.tsx
│   └── primitives/
│       ├── Button.tsx
│       ├── IconButton.tsx
│       ├── Badge.tsx
│       ├── Panel.tsx
│       ├── Skeleton.tsx
│       ├── EmptyState.tsx
│       ├── ErrorState.tsx
│       └── UnavailablePage.tsx
├── features/
│   └── command-center/
│       ├── queries.ts       # useRuntimeStatus, useTasks, useApprovals, useCapabilities
│       ├── mutations.ts     # useCreateTask, useCancelTask, useDecideApproval
│       └── CommandCenterView.tsx
├── lib/
│   ├── api/
│   │   ├── client.ts        # fetch wrapper + Zod parse
│   │   ├── contracts.ts     # all Zod schemas
│   │   └── errors.ts        # AtlasApiError type
│   ├── events/
│   │   ├── socket.ts        # EventSource with reconnect + dedup
│   │   └── reconcile.ts     # useRuntimeReconcile hook
│   ├── query-keys.ts        # all TanStack Query key factories
│   └── formatters.ts        # dates, costs, state labels, humanise
├── styles/
│   ├── tokens.css           # OKLCH palette + spacing + radius + easing
│   └── motion.css           # transitions + prefers-reduced-motion
├── tests/
│   ├── fixtures/            # JSON snapshots of each API response shape
│   ├── contracts/           # Zod schema parse tests
│   ├── components/          # Vitest + Testing Library
│   └── e2e/                 # Playwright
├── public/
│   └── atlas-mark.svg
├── package.json
├── tsconfig.json
├── next.config.ts
├── vitest.config.ts
└── playwright.config.ts
```


### `styles/tokens.css` — complete token file

```css
:root {
  /* Surfaces */
  --ink-950: oklch(10% .018 278);
  --ink-900: oklch(14% .024 278);
  --ink-850: oklch(18% .030 278);
  --ink-800: oklch(22% .036 278);
  --ink-700: oklch(29% .042 278);
  --line:    oklch(34% .035 278);

  /* Text */
  --paper-100: oklch(94% .012 82);
  --paper-300: oklch(80% .020 82);
  --paper-500: oklch(62% .025 82);

  /* Accent */
  --royal-500: oklch(58% .16 292);
  --royal-400: oklch(68% .14 292);
  --gold-500:  oklch(76% .13 82);
  --gold-400:  oklch(84% .12 82);
  --jade-400:  oklch(73% .13 162);
  --ember-400: oklch(70% .16 35);
  --danger-400:oklch(68% .18 22);

  /* Spacing */
  --space-xs:  4px;
  --space-sm:  8px;
  --space-md:  16px;
  --space-lg:  24px;
  --space-xl:  32px;
  --space-2xl: 48px;
  --space-3xl: 64px;

  /* Radius */
  --radius-sm: 6px;
  --radius-md: 10px;
  --radius-lg: 16px;

  /* Easing */
  --ease-out: cubic-bezier(.16, 1, .3, 1);

  /* Typography scale */
  --text-xs:   12px;
  --text-sm:   14px;
  --text-base: 16px;
  --text-lg:   18px;
  --text-xl:   22px;
  --text-2xl:  28px;
  --text-3xl:  36px;
}
```

### `styles/motion.css` — transitions

```css
/* All transitions off for users who prefer reduced motion */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: .01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: .01ms !important;
  }
}

/* Standard durations as CSS custom properties */
:root {
  --duration-fast:      120ms;
  --duration-panel:     240ms;
  --duration-workspace: 400ms;
}
```

### `lib/api/contracts.ts` — schema design

All schemas are verbatim from the Phase One document section 5, with one addition:

```typescript
export const AuditEventSchema = z.object({
  id: z.number().int(),
  correlation_id: z.string(),
  ts: z.string().datetime(),
  actor: z.string(),
  action: z.string(),
  tool: z.string().nullable(),
  tier: z.number().int().nullable(),
  decision: z.string().nullable(),
  outcome: z.string().nullable(),
  cost_tokens: z.number().int(),
  cost_usd: z.number(),
  payload_summary: z.string().nullable(),
});
```

### `lib/api/client.ts` — design

```typescript
const API_BASE = process.env.NEXT_PUBLIC_ATLAS_API_URL ?? "http://localhost:8730/api/v1";

export class AtlasApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    public detail: string,
    public requestId: string | null,
  ) { super(`[${status}] ${code}: ${detail}`); }
}

async function request<T extends z.ZodTypeAny>(
  path: string, schema: T, init?: RequestInit,
): Promise<z.infer<T>> {
  const requestId = crypto.randomUUID();
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      "X-Request-ID": requestId,
      ...init?.headers,
    },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new AtlasApiError(res.status, body.error ?? "unknown",
      body.detail ?? "", body.request_id ?? null);
  }
  return schema.parse(await res.json());
}
```

The `atlasApi` object then wraps `request()` for every endpoint. Idempotency
keys are generated by callers using `crypto.randomUUID()`.


### `lib/query-keys.ts`

```typescript
export const queryKeys = {
  runtimeStatus:  () => ["runtime", "status"] as const,
  runtimeHealth:  () => ["runtime", "health"] as const,
  tasks:          (filters?: object) => ["tasks", filters] as const,
  task:           (id: string) => ["tasks", id] as const,
  taskEvents:     (id: string) => ["tasks", id, "events"] as const,
  approvals:      () => ["approvals", "pending"] as const,
  audit:          (filters?: object) => ["audit", filters] as const,
  capabilities:   () => ["capabilities"] as const,
  providersHealth:() => ["providers", "health"] as const,
};
```

### `features/command-center/queries.ts`

```typescript
export function useRuntimeStatus() {
  return useQuery({
    queryKey: queryKeys.runtimeStatus(),
    queryFn: () => atlasApi.runtimeStatus(),
    refetchInterval: 5_000,
    retry: false,        // offline state shown immediately, not after retries
  });
}

export function useApprovals() {
  return useQuery({
    queryKey: queryKeys.approvals(),
    queryFn: () => atlasApi.approvals(),
    refetchInterval: 3_000,  // fast poll for approval inbox
  });
}
```

### `features/command-center/mutations.ts`

```typescript
export function useCreateTask() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: string) =>
      atlasApi.createTask({
        request,
        idempotency_key: crypto.randomUUID(),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.tasks() });
      queryClient.invalidateQueries({ queryKey: queryKeys.runtimeStatus() });
    },
  });
}

export function useDecideApproval() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, decision }: { id: string; decision: "approve" | "deny" }) =>
      atlasApi.decideApproval(id, decision, crypto.randomUUID()),
    onSuccess: () => {
      // NO optimistic update — wait for server response before updating UI
      queryClient.invalidateQueries({ queryKey: queryKeys.approvals() });
      queryClient.invalidateQueries({ queryKey: queryKeys.runtimeStatus() });
    },
  });
}
```

Note the explicit comment: no optimistic update on approval decisions. This is
a hard rule from the Phase One spec. The UI waits for the server.


### Component designs

#### `AppShell.tsx`

```typescript
// Renders: Sidebar (desktop) | MobileNav (mobile) | main content area
// Uses CSS Grid: `grid-template-columns: 220px 1fr` on desktop
//               `grid-template-rows: 1fr auto` on mobile
// SystemStatusStrip is fixed at top, z-index above content
```

Responsive breakpoint: `768px`. Below that, Sidebar hides, MobileNav
appears as a fixed bottom bar with the 5 primary nav items. Sidebar on desktop
is always visible — no toggle. Sidebar width: `220px`, non-resizable.

#### `Sidebar.tsx`

Nav items in order (from Phase One doc):
1. Command Center (`/`) — Lucide `Home`
2. Live Run (`/activity`) — Lucide `Activity`
3. Tasks (`/tasks`) — Lucide `ListTodo`
4. Approvals (`/approvals`) — Lucide `ShieldCheck`, shows count badge
5. Memory (`/memory`) — Lucide `Brain`
6. Capabilities (`/capabilities`) — Lucide `Zap`
7. Audit (`/audit`) — Lucide `ScrollText`
8. Settings (`/settings`) — Lucide `Settings`

Bottom of sidebar: `EventConnectionBadge` (live/reconnecting/closed dot).
Top: ATLAS wordmark in Cormorant Garamond 600, 22px.
Active item: `--royal-400` left border + `--ink-800` background.

#### `SystemStatusStrip.tsx`

```typescript
// Always rendered inside AppShell above all content
// Height: 36px, background: --ink-900, border-bottom: 1px solid --line
// Content:
//   [status dot] ATLAS ONLINE · ollama ready · 2 approvals · $0.04 today
// On click: opens HealthDrawer (aside panel, width 400px, slides from right)
// Disconnected state: --danger-400 dot, "ATLAS OFFLINE · reconnecting in 3s"
// Kill switch active: --ember-400 dot, "KILL SWITCH ACTIVE"
```

The status dot is the only animated element — a 2s breathing animation in CSS,
disabled by `prefers-reduced-motion`.

#### `CommandComposer.tsx`

```typescript
// Single <textarea> (auto-growing, max 8 lines)
// Placeholder: "What should ATLAS do?"
// Submit: Enter key (Shift+Enter = newline), or Send button
// Global shortcut: ⌘K / Ctrl+K focuses the textarea from anywhere
// Disabled states:
//   - runtimeStatus.state !== "ready": shows "ATLAS is not ready" below
//   - kill_switch_active: shows "Kill switch is active — revive to run tasks"
//   - createTask.isPending: loading spinner on button, textarea disabled
// After successful submit:
//   - clears textarea
//   - renders <IntentReceipt task={createdTask} />
```

#### `IntentReceipt.tsx`

```typescript
// Shown below composer after successful task creation
// Content:
//   Task <monospace short-id> submitted from api
//   [state badge: "created"] [risk if plan.risk !== "low"]
//   "Approval may be required" — only if pending_approval_count increases
// Auto-hides after 30s or when user submits next task
// No spinner — the task is running in background, timeline shows progress
```

#### `ApprovalCard.tsx`

```typescript
// One card per pending approval
// Header: capability · operation · Tier-N badge
// Body: exact preview text (pre-formatted, monospace, truncated at 20 lines
//        with "Show full preview" expand)
// Warnings: each warning on its own line with --ember-400 icon
// Expiry: countdown timer in --ember-400 if < 5 min remaining
// Footer:
//   [Approve once]  [Deny]
//   Both buttons disabled while useMutation.isPending
//   No optimistic update — buttons re-enable only after server response
//   "Approve once" text is exact — never "Approve all" or just "Approve"
```

#### `CapabilityPosture.tsx`

```typescript
// Compact status rail — one row per capability
// Columns: name | state badge | "N providers" | requires_auth icon
// State badge colours:
//   ready       → --jade-400
//   degraded    → --ember-400
//   unavailable → --danger-400
//   planned     → --paper-500 (muted)
// No interactive controls — read-only display in Phase One
// "planned" capabilities are visually de-emphasised but present and labelled
```

#### `Badge.tsx` — state vocabulary

Maps backend state strings to display:

| value              | label            | color         |
|--------------------|------------------|---------------|
| `ready`            | ready            | `--jade-400`  |
| `running`          | running          | `--royal-400` |
| `planning`         | planning         | `--royal-400` |
| `reasoning`        | thinking         | `--royal-400` |
| `waiting_tool`     | waiting          | `--ember-400` |
| `completed`        | completed        | `--jade-400`  |
| `failed`           | failed           | `--danger-400`|
| `cancelled`        | cancelled        | `--paper-500` |
| `degraded`         | degraded         | `--ember-400` |
| `unavailable`      | unavailable      | `--danger-400`|
| `planned`          | planned          | `--paper-500` |
| `pending`          | pending          | `--gold-500`  |
| `approved`         | approved         | `--jade-400`  |
| `denied`           | denied           | `--danger-400`|
| `expired`          | expired          | `--paper-500` |

Badge always has a text label — never color-only.


### `lib/events/socket.ts` — SSE design

```typescript
export function connectRuntimeEvents(
  onEvent: (e: TaskEvent) => void,
  onStatus: (s: "connected" | "reconnecting" | "closed") => void,
): () => void {
  const url = `${API_BASE}/events`;
  const seen = new Set<string>();
  let es: EventSource | null = null;
  let stopped = false;
  let delay = 500;

  const connect = () => {
    if (stopped) return;
    es = new EventSource(url, { withCredentials: true });

    es.onopen = () => { delay = 500; onStatus("connected"); };

    es.onmessage = (msg) => {
      const parsed = TaskEventSchema.safeParse(JSON.parse(msg.data));
      if (!parsed.success) return;
      const { event_id } = parsed.data;
      if (seen.has(event_id)) return;
      seen.add(event_id);
      if (seen.size > 500) seen.delete(seen.values().next().value!);
      onEvent(parsed.data);
    };

    es.onerror = () => {
      es?.close();
      if (stopped) return;
      onStatus("reconnecting");
      setTimeout(connect, delay);
      delay = Math.min(delay * 2, 10_000);
    };
  };

  connect();
  return () => { stopped = true; es?.close(); onStatus("closed"); };
}
```

### `lib/events/reconcile.ts` — reconcile hook

```typescript
export function useRuntimeReconcile() {
  const queryClient = useQueryClient();

  useEffect(() => {
    const invalidate = () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.runtimeStatus() });
      queryClient.invalidateQueries({ queryKey: queryKeys.tasks() });
      queryClient.invalidateQueries({ queryKey: queryKeys.approvals() });
    };

    // Invalidate on tab focus (user returns to tab after being away)
    window.addEventListener("focus", invalidate);

    // Connect SSE
    const disconnect = connectRuntimeEvents(
      (event) => {
        // Invalidate relevant queries when we see certain event types
        if (["task.completed","task.failed","task.created"].includes(event.event_type)) {
          queryClient.invalidateQueries({ queryKey: queryKeys.tasks() });
          queryClient.invalidateQueries({ queryKey: queryKeys.runtimeStatus() });
        }
      },
      (status) => {
        if (status === "connected") invalidate();  // resync on reconnect
      },
    );

    return () => {
      window.removeEventListener("focus", invalidate);
      disconnect();
    };
  }, [queryClient]);
}
```

This hook is called once in `app/layout.tsx`. It keeps REST state fresh
without the frontend treating the SSE stream as the source of truth.

### `next.config.ts`

```typescript
import type { NextConfig } from "next";
const nextConfig: NextConfig = {
  async rewrites() {
    // In dev, proxy /api/v1/* to the Python backend at port 8730
    // This avoids CORS issues during development
    return [
      {
        source: "/api/v1/:path*",
        destination: "http://localhost:8730/api/v1/:path*",
      },
    ];
  },
};
export default nextConfig;
```

With this proxy, `NEXT_PUBLIC_ATLAS_API_URL` can be set to `/api/v1` in dev
and the Next.js dev server handles the CORS proxying transparently.

### `package.json` — key scripts

```json
{
  "scripts": {
    "dev":      "next dev --turbopack",
    "build":    "next build",
    "start":    "next start",
    "lint":     "next lint",
    "typecheck":"tsc --noEmit",
    "test":     "vitest run",
    "test:watch":"vitest",
    "test:e2e": "playwright test",
    "check":    "npm run typecheck && npm run lint && npm run test"
  }
}
```

### Test fixtures design

`frontend/tests/fixtures/` contains one JSON file per API response type:
- `runtime-status.json` — a valid `RuntimeStatusResponse`
- `runtime-health.json` — a valid `RuntimeHealthResponse` with all check states
- `task-created.json`, `task-running.json`, `task-completed.json`, `task-failed.json`
- `tasks-list.json` — array of 5 tasks in mixed states
- `task-events.json` — array of 12 events across all episode kinds
- `approvals-pending.json` — two pending approvals with full preview text
- `capabilities.json` — all 5 capability states (ready, degraded, unavailable, planned ×2)
- `audit-events.json` — 10 audit events with redacted payloads

Contract tests in `tests/contracts/` parse every fixture through the Zod schema:
```typescript
it("parses runtime-status fixture", () => {
  const data = readFixture("runtime-status.json");
  expect(() => RuntimeStatusSchema.parse(data)).not.toThrow();
});
```

Any schema drift between backend and frontend causes an explicit test failure here
before any React component is involved.

---

## Data flow diagram

```
User types request
    │
    ▼
CommandComposer (frontend)
    │  POST /api/v1/tasks
    ▼
routes_tasks.py (FastAPI)
    │  InboundEvent → atlas.orchestrator.run() [background task]
    │  writes task row to DB
    ▼
TaskResponse → frontend
    │
    ▼
IntentReceipt renders
    │
SSE stream pushes OrchestratorEvents
    │  connectRuntimeEvents() receives events
    │  deduplicates by event_id
    │  invalidates TanStack Query cache on key events
    ▼
useRuntimeStatus / useTasks refetch from REST
    │
    ▼
ActivityTimeline re-renders with new episodes
ApprovalInbox appears if approval required
```

REST is always authoritative. SSE is a low-latency invalidation signal.
