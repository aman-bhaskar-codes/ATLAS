# Tasks — ATLAS Phase One: Frontend Foundation & Command Center

- [ ] 1. Backend pre-conditions: fix InferenceRuntime.close(), approval get_event_loop(), and Orchestrator task persistence
  - In `src/atlas/intelligence/runtime/inference.py`, add `async def close(self) -> None: await self._providers.close()` as a public method on `InferenceRuntime`
  - In `src/atlas/intelligence/gateway.py`, change `ModelGateway.close()` from `await self._runtime._providers.close()` to `await self._runtime.close()`
  - In `src/atlas/capabilities/notification/approval.py`, replace `asyncio.get_event_loop().create_future()` with `asyncio.get_running_loop().create_future()`
  - In `src/atlas/capabilities/notification/approval.py`, add `self._requests: dict[str, ApprovalRequest] = {}` to `__init__`, store `self._requests[req.id] = req` at start of `request()`, and remove it in the `finally` block alongside `_pending`
  - In `src/atlas/orchestration/orchestrator.py`, add `import json` at the top, add `db: Database` parameter to `__init__` and store as `self._db`, add `from atlas.infra.db import Database` import
  - In `Orchestrator.run()`, after the `Task` domain object is created, insert a DB row: `await self._db.conn.execute("INSERT OR IGNORE INTO tasks(id, source, state, payload, idempotency_key, created_ts, updated_ts) VALUES (?,?,?,?,?,?,?)", (task.id, task.source, "created", json.dumps({"request": task.request, "correlation_id": task.correlation_id}), None, task.created_ts.isoformat(), task.created_ts.isoformat()))` followed by `await self._db.conn.commit()`
  - In `Orchestrator.run()`, in the `finally` block after `self._cancels.pop(task.id, None)`, add: `await self._db.conn.execute("UPDATE tasks SET state=?, updated_ts=? WHERE id=?", (machine.state.value, self._clock.now().isoformat(), task.id))` followed by `await self._db.conn.commit()`
  - In `src/atlas/app.py`, pass `db=db` to the `Orchestrator(...)` constructor call
  - Run `uv run pytest tests/ -q` — all 84 tests must still pass
  - Run `uv run mypy src/ --strict` — zero errors
  - _Requirements: PRE-1, PRE-2, PRE-3_
  - _Demo: `uv run atlas run "hello"` completes and a row appears in the tasks table: `sqlite3 .atlas/atlas.db "SELECT id, state FROM tasks LIMIT 5"`_


- [ ] 2. Add FastAPI and uvicorn dependencies to the Python project
  - In `atlas/pyproject.toml`, add `"fastapi>=0.115"` and `"uvicorn[standard]>=0.30"` to the `dependencies` list
  - Run `cd atlas && uv sync` to install and lock the new dependencies
  - In `atlas/Justfile`, add the `serve` target: `uv run uvicorn atlas.interfaces.api.app:create_app --factory --host 127.0.0.1 --port 8730 --reload`
  - Verify uvicorn is importable: `uv run python -c "import uvicorn; import fastapi; print('ok')"`
  - _Requirements: REQ-1.10_
  - _Demo: `just serve` starts without error (will fail on missing module until Task 3, but uvicorn itself must resolve)_


- [ ] 3. Create the FastAPI adapter package skeleton with app factory, DI, and error handling
  - Create `src/atlas/interfaces/api/__init__.py` (empty)
  - Create `src/atlas/interfaces/api/errors.py` with `atlas_exception_handler(request, exc)` that maps `DeniedError→403`, `HaltedError→503`, `BudgetExceededError→402`, `KeyError→404`, and all others to `500`; response body is `{"error": code, "detail": str(exc)[:200], "request_id": request.headers.get("X-Request-ID")}` — no tracebacks, no module paths
  - Create `src/atlas/interfaces/api/dependencies.py` with `get_atlas(request: Request) -> Atlas` reading `request.app.state.atlas`
  - Create `src/atlas/interfaces/api/app.py` with `create_app() -> FastAPI`: lifespan context manager calls `build()`, `atlas.start()`, stores in `app.state.atlas` and `app.state.version`, calls `atlas.close()` on shutdown; CORSMiddleware with `allow_origins=["http://localhost:3000"]`, `allow_credentials=True`, `allow_methods=["GET","POST"]`; registers `atlas_exception_handler` for `Exception`
  - Verify the factory is importable: `uv run python -c "from atlas.interfaces.api.app import create_app; print('ok')"`
  - Run `uv run mypy src/atlas/interfaces/api/ --strict` — zero errors
  - _Requirements: REQ-1.1, REQ-1.2, REQ-1.3, REQ-1.8, REQ-1.9_
  - _Demo: `just serve` starts uvicorn and the process stays alive — `curl http://localhost:8730/` returns 404 (no routes yet, that is correct)_

- [ ] 4. Create API schemas and runtime read routes
  - Create `src/atlas/interfaces/api/schemas.py` with all Pydantic response models exactly as specified in REQ-1.4: `RuntimeStatusResponse`, `HealthCheckResponse`, `RuntimeHealthResponse`, `TaskResponse`, `TaskEventResponse`, `ApprovalResponse`, `CapabilityResponse`, `AuditEventResponse`; all fields typed strictly, no `Any` in response models
  - Create `src/atlas/interfaces/api/routes_runtime.py` with:
    - `GET /api/v1/runtime/status` — assembles `RuntimeStatusResponse` from `atlas.killswitch.is_active()`, `atlas.audit.cost_today()`, `atlas.audit.tail(1)` for `last_audit_at`, `len(atlas.notification_platform._approvals._pending)` for `pending_approval_count`, active task counter from `app.state`
    - `GET /api/v1/runtime/health` — calls `run_doctor(atlas)` from `atlas.diagnostics.doctor`, maps `CheckResult` list to `RuntimeHealthResponse`; `overall` is `"healthy"` if all pass, `"degraded"` if any warn, `"unavailable"` if any fail
    - `POST /api/v1/runtime/kill-switch` — requires `X-Confirm: true` header, calls `atlas.killswitch.trip()`, returns `{"status": "tripped"}`
    - `POST /api/v1/runtime/kill-switch/reset` — requires `X-Confirm: true` header, calls `atlas.killswitch.reset()`, returns `{"status": "reset"}`
  - Register the runtime router in `app.py` under prefix `/api/v1`
  - Run `just serve` and smoke test: `curl http://localhost:8730/api/v1/runtime/status` returns valid JSON; `curl http://localhost:8730/api/v1/runtime/health` returns checks array
  - _Requirements: REQ-1.4, REQ-1.5_
  - _Demo: both GET endpoints return valid JSON matching their Pydantic schemas — verify with `python -c "import requests, json; print(json.dumps(requests.get('http://localhost:8730/api/v1/runtime/status').json(), indent=2))"`_


- [ ] 5. Create task, audit, capability, and provider health routes
  - Create `src/atlas/interfaces/api/routes_tasks.py` with:
    - `GET /api/v1/tasks?limit=20&cursor=0` — queries `tasks` table via `atlas.db.conn`, parses `request` from `json.loads(row["payload"])["request"]`, returns `list[TaskResponse]`
    - `GET /api/v1/tasks/{task_id}` — same query filtered by `id`, raises `KeyError` (→ 404) if not found
    - `GET /api/v1/tasks/{task_id}/events` — queries `episodes` table by `correlation_id` looked up from `tasks`; maps each row to `TaskEventResponse` using the `_humanise(kind, role, content)` helper; `event_id = str(row["id"])`, `schema_version = 1`
    - `POST /api/v1/tasks` — validates `X-Idempotency-Key` header (min 16 chars, 422 if missing/short); body: `{"request": str, "source": "api", "idempotency_key": str}`; creates `InboundEvent` and runs `atlas.orchestrator.run(event)` in `asyncio.create_task()` (background, non-blocking); increments `app.state.active_task_count` before launch, decrements in a callback; returns `TaskResponse` with `state="created"`
    - `POST /api/v1/tasks/{task_id}/cancel` — calls `atlas.orchestrator.cancel(task_id)`, returns updated `TaskResponse`
  - Create `src/atlas/interfaces/api/routes_audit.py` with `GET /api/v1/audit?limit=50&correlation_id=` — calls `atlas.audit.tail(limit)` or `atlas.audit.by_correlation(correlation_id)`; maps rows to `AuditEventResponse`; fetches `payloads` table for each `payload_id` and returns only the key names joined as string for `payload_summary` — never the body
  - Create `src/atlas/interfaces/api/routes_capabilities.py` with:
    - `GET /api/v1/capabilities` — iterates `atlas.cap_registry.all()`, for each spec queries `atlas.cap_providers` and `atlas.cap_health` to compute `providers`, `healthy_providers`, and `state`
    - `GET /api/v1/providers/health` — merges `atlas.cap_health.snapshot()` and `atlas.gateway.health()` into a single dict response
  - Register all three routers in `app.py` under prefix `/api/v1`
  - Run `uv run mypy src/atlas/interfaces/api/ --strict` — zero errors
  - Smoke test all new endpoints with curl after `just serve`
  - _Requirements: REQ-1.5_
  - _Demo: `curl http://localhost:8730/api/v1/tasks` returns `[]` (empty until a task runs); `curl http://localhost:8730/api/v1/capabilities` returns the 4 registered capabilities (knowledge, email, calendar, contacts) with correct state fields_

- [ ] 6. Create approval routes and SSE event stream
  - Create `src/atlas/interfaces/api/routes_approvals.py` with:
    - `GET /api/v1/approvals/pending` — reads `atlas.notification_platform._approvals._requests` (the new dict added in Task 1) and `._pending`; returns only entries where the future is not done; maps each `ApprovalRequest` to `ApprovalResponse` with `status="pending"`, `tier=2` (approval requests are always Tier 2), `warnings=[]` unless preview contains "⚠️" lines (parse them out)
    - `POST /api/v1/approvals/{approval_id}/decide` — validates `X-Idempotency-Key` header; body: `{"decision": "approve"|"deny", "idempotency_key": str}`; calls `atlas.notification_platform._approvals.resolve(approval_id, approved=decision=="approve")`; returns `ApprovalResponse` with updated `status`; if `approval_id` not in `_pending`, return 404
  - Create `src/atlas/interfaces/api/events.py` with `runtime_event_stream(request, atlas)` returning `StreamingResponse` with `media_type="text/event-stream"`: subscribes an async handler to `atlas.bus` on topic `"orchestrator"`; handler pushes `f"id: {uuid4().hex}\ndata: {event.model_dump_json()}\n\n"` to a `Queue(maxsize=200)`; generator yields from the queue with 15s timeout ping comments; removes handler on disconnect
  - Add `GET /api/v1/events` route in `app.py` that calls `runtime_event_stream(request, atlas)`
  - Register approvals router in `app.py` under prefix `/api/v1`
  - Test SSE stream: `curl -N http://localhost:8730/api/v1/events` — should see `": ping 1"` comment every 15 seconds
  - _Requirements: REQ-1.5, REQ-1.6, REQ-1.7_
  - _Demo: Run `atlas run "hello"` in one terminal while `curl -N http://localhost:8730/api/v1/events` runs in another — SSE events for `task.created`, `planning.started`, `task.completed` appear in the curl output_


- [ ] 7. Scaffold the Next.js frontend application
  - From the ATLAS project root (`/Users/amanbhaskar/Agentic & AI Agent Projects/ATLAS/`), run: `npx create-next-app@latest frontend --typescript --tailwind --eslint --app --src-dir no --import-alias "@/*"` — answer "no" to all optional extras except TypeScript and ESLint
  - After scaffold, install exact required dependencies: `cd frontend && npm install @tanstack/react-query@5 zod@3 lucide-react` and dev deps: `npm install -D vitest@2 @vitest/coverage-v8 @testing-library/react@16 @testing-library/jest-dom @testing-library/user-event jsdom @playwright/test`
  - Remove all Next.js default boilerplate: clear `app/page.tsx` content, delete `app/globals.css` (will be replaced), delete `public/next.svg` and `public/vercel.svg`
  - Create `frontend/.env.local` with `NEXT_PUBLIC_ATLAS_API_URL=/api/v1` and `NEXT_PUBLIC_ATLAS_ENV=development`
  - Create `frontend/.env.local` in `.gitignore` if not already present
  - Configure `next.config.ts` with the rewrites proxy: source `/api/v1/:path*` → destination `http://localhost:8730/api/v1/:path*`
  - Add `vitest.config.ts` with `environment: "jsdom"`, `globals: true`, `setupFiles: ["./tests/setup.ts"]`
  - Add `playwright.config.ts` with `baseURL: "http://localhost:3000"`, `testDir: "./tests/e2e"`
  - Create `frontend/tests/setup.ts` that imports `@testing-library/jest-dom`
  - Create `frontend/tests/fixtures/`, `frontend/tests/contracts/`, `frontend/tests/components/`, `frontend/tests/e2e/` directories with `.gitkeep`
  - Update `package.json` scripts: `"typecheck": "tsc --noEmit"`, `"test": "vitest run"`, `"test:watch": "vitest"`, `"test:e2e": "playwright test"`, `"check": "npm run typecheck && npm run lint && npm run test"`
  - Run `npm run dev` — app starts at `localhost:3000` with no errors
  - _Requirements: REQ-2.1, REQ-2.2, REQ-2.3_
  - _Demo: `npm run dev` shows Next.js at localhost:3000; `npm run typecheck` passes; `npm run test` passes (zero tests, that is expected at this stage)_

- [ ] 8. Implement the design system: tokens, motion, typography, and primitive components
  - Delete Tailwind's default `globals.css`; create `frontend/app/globals.css` that only imports `../styles/tokens.css` and `../styles/motion.css` and sets `body { background: var(--ink-950); color: var(--paper-100); font-family: var(--font-plex-sans); }` and `* { box-sizing: border-box; margin: 0; }`
  - Create `frontend/styles/tokens.css` with the complete OKLCH palette, spacing, radius, easing, and type scale variables exactly as specified in the design doc
  - Create `frontend/styles/motion.css` with the `prefers-reduced-motion` block that disables all animations, plus `--duration-fast: 120ms`, `--duration-panel: 240ms`, `--duration-workspace: 400ms`
  - In `frontend/app/layout.tsx`, load all three fonts via `next/font/google`: `Cormorant_Garamond` (subsets: latin, weights: 500 600), `IBM_Plex_Sans` (subsets: latin, weights: 400 500 600), `IBM_Plex_Mono` (subsets: latin, weights: 400 500); assign each as a CSS variable (`--font-cormorant`, `--font-plex-sans`, `--font-plex-mono`) on the `<html>` element
  - Create `frontend/components/primitives/Button.tsx` — variants: `primary` (royal fill), `ghost` (transparent), `danger` (danger-400); sizes: `sm`, `md`; all 6 states: default, hover (`transform: scale(1.01)`, 120ms), focus-visible (2px `--royal-400` ring), active (scale down), disabled (0.4 opacity, cursor not-allowed), loading (spinner icon replaces content, disabled); never uses `outline: none` without `:focus-visible` replacement
  - Create `frontend/components/primitives/Badge.tsx` — accepts a `state` prop from the full 15-value vocabulary (ready, running, planning, reasoning, waiting_tool, completed, failed, cancelled, degraded, unavailable, planned, pending, approved, denied, expired); renders colored dot + text label; dot color from design doc's state→color mapping; always renders the text label alongside the dot — never color-only
  - Create `frontend/components/primitives/Panel.tsx` — `background: var(--ink-900)`, `border: 1px solid var(--line)`, `border-radius: var(--radius-md)`; variant `elevated` uses `--ink-850`
  - Create `frontend/components/primitives/Skeleton.tsx` — same dimensions as its content placeholder; pulse animation via `@keyframes pulse { 0%,100% { opacity:.4 } 50% { opacity:.8 } }`; animation paused when `prefers-reduced-motion`
  - Create `frontend/components/primitives/EmptyState.tsx` — icon (Lucide), heading (IBM Plex Sans 500 18px), body (paper-300), optional action button
  - Create `frontend/components/primitives/ErrorState.tsx` — heading, message, Retry button that calls `onRetry()` prop
  - Create `frontend/components/primitives/UnavailablePage.tsx` — full-page centered layout; shows capability name + "Coming in Phase Fx" label in paper-500; used for all stub routes
  - Write Vitest tests in `frontend/tests/components/` for `Button` (renders, disabled state, loading state) and `Badge` (all 15 state values render with text label)
  - Run `npm run test` — all component tests pass; `npm run typecheck` — zero errors
  - _Requirements: REQ-3.1, REQ-3.2, REQ-3.3, REQ-3.4_
  - _Demo: Open `localhost:3000` — background is `oklch(10% .018 278)` deep indigo-black; text is warm paper-white; fonts loaded (verify in DevTools Network tab); no Tailwind blue anywhere_


- [ ] 9. Implement API contracts, client, and query infrastructure
  - Create `frontend/lib/api/errors.ts` exporting `class AtlasApiError extends Error` with fields `status: number`, `code: string`, `detail: string`, `requestId: string | null`
  - Create `frontend/lib/api/contracts.ts` with all Zod schemas exactly as in design doc section 5: `RuntimeStatusSchema`, `HealthCheckSchema`, `RuntimeHealthSchema`, `TaskSchema`, `TaskEventSchema`, `ApprovalSchema`, `CapabilitySchema`, `AuditEventSchema`, plus command schemas `CreateTaskSchema` and `ApprovalDecisionSchema`; export all inferred TypeScript types
  - Create `frontend/lib/api/client.ts` with the `request<T>()` helper function using `fetch`, `credentials: "include"`, `X-Request-ID: crypto.randomUUID()`, Zod `.parse()` on response (not `.safeParse()`), throws `AtlasApiError` on non-ok responses; export `atlasApi` object with methods: `runtimeStatus()`, `runtimeHealth()`, `tasks(limit?)`, `task(id)`, `taskEvents(id)`, `approvals()`, `capabilities()`, `providersHealth()`, `audit(filters?)`, `createTask({request, idempotency_key})`, `cancelTask(taskId, idempotency_key)`, `decideApproval(approvalId, decision, idempotency_key)`, `tripKillSwitch()`, `resetKillSwitch()`
  - Create `frontend/lib/query-keys.ts` with the `queryKeys` factory object for all 9 resource types
  - Create `frontend/lib/formatters.ts` with: `formatCost(usd: number): string` (e.g. `"$0.04"`), `formatRelativeTime(iso: string): string` (e.g. `"2 min ago"`), `formatTaskState(state: string): string` (human label from Badge vocabulary), `formatDuration(ms: number): string` (e.g. `"1.2s"`), `truncateId(id: string): string` (last 8 chars with `…` prefix)
  - Create `frontend/tests/fixtures/` JSON files: `runtime-status.json`, `runtime-health.json`, `task-created.json`, `task-running.json`, `task-completed.json`, `task-failed.json`, `tasks-list.json`, `task-events.json`, `approvals-pending.json`, `capabilities.json`, `audit-events.json` — each is a valid JSON value matching its schema; `approvals-pending.json` must include a full multi-line `preview` field with email send preview text as specified in the Phase One doc
  - Create `frontend/tests/contracts/` Vitest tests that parse every fixture through its Zod schema — one test per fixture; any failure means schema drift
  - Run `npm run test` — all 11 contract tests pass; `npm run typecheck` — zero errors
  - _Requirements: REQ-4.1, REQ-4.2, REQ-4.3_
  - _Demo: `npm run test` shows 11 passing contract tests in `tests/contracts/`_

- [ ] 10. Implement the SSE event client and reconciliation hook
  - Create `frontend/lib/events/socket.ts` with `connectRuntimeEvents(onEvent, onStatus)` exactly as in design doc: `EventSource` with `withCredentials: true`, exponential backoff (500ms → 10s), deduplication set of 500 IDs, returns a cleanup function; validates each incoming message with `TaskEventSchema.safeParse()` and drops invalid events silently with `console.warn`
  - Create `frontend/lib/events/reconcile.ts` with `useRuntimeReconcile()` hook: sets up the SSE connection via `connectRuntimeEvents`, invalidates `runtimeStatus`, `tasks`, and `approvals` query keys on connect and on `task.completed`/`task.failed`/`task.created` events, adds a `window.addEventListener("focus", invalidate)` for tab-focus resync, returns SSE connection status as `"connected" | "reconnecting" | "closed"`
  - Write Vitest tests in `frontend/tests/events/` for `connectRuntimeEvents`: mock `EventSource` globally; test that valid events call `onEvent`, invalid events are dropped, `onStatus("reconnecting")` fires on error, `onStatus("connected")` fires on open, cleanup function closes the connection, duplicate `event_id` values are deduplicated
  - Run `npm run test` — all event tests pass; `npm run typecheck` — zero errors
  - _Requirements: REQ-5.1, REQ-5.2_
  - _Demo: All event tests green. No TypeScript errors._


- [ ] 11. Build the application shell: layout, sidebar, mobile nav, and status strip
  - Wrap the root `app/layout.tsx` with a `<QueryClientProvider>` (TanStack Query), call `useRuntimeReconcile()` once at this level, and render `<AppShell>` around `{children}`
  - Create `frontend/components/shell/AppShell.tsx` — CSS Grid layout: `220px 1fr` on desktop (≥768px), single column on mobile; `<SystemStatusStrip>` fixed at top (height 36px, z-index 50); `<Sidebar>` on the left column (desktop only); `<MobileNav>` fixed at bottom (mobile only); `<main>` in right column with padding `var(--space-lg)`
  - Create `frontend/components/shell/Sidebar.tsx` — ATLAS wordmark at top in Cormorant Garamond 600 22px using `--gold-400` color; nav items list with icon + text label for all 8 routes (Command Center, Live Run, Tasks, Approvals, Memory, Capabilities, Audit, Settings) using `next/link`; active item detection via `usePathname()`; active item style: `border-left: 2px solid var(--royal-400)`, `background: var(--ink-800)`; Approvals item shows a count badge when `pendingApprovalCount > 0` (reads from `useRuntimeStatus()`); `<EventConnectionBadge>` at the bottom
  - Create `frontend/components/shell/MobileNav.tsx` — fixed bottom bar, height 56px, `background: var(--ink-900)`, `border-top: 1px solid var(--line)`; shows 5 primary items only: Command Center, Live Run, Approvals (with badge), Capabilities, Settings; icon-only with visible text label below (no icon-only mystery controls)
  - Create `frontend/components/runtime/EventConnectionBadge.tsx` — small dot + text: `"live"` (jade), `"reconnecting"` (ember, breathing animation), `"offline"` (danger); reads connection status from a prop passed down from the reconcile hook; breathing animation disabled by `prefers-reduced-motion`
  - Create `frontend/components/shell/SystemStatusStrip.tsx` — fixed top bar; reads `useRuntimeStatus()` query; renders `ATLAS ONLINE · <model status> · <N approvals> · $<cost> today` when ready; `ATLAS OFFLINE · reconnecting…` with danger color when query fails or status is not `"ready"`; `KILL SWITCH ACTIVE` with ember color when `kill_switch_active: true`; entire strip is a `<button>` that opens `<HealthDrawer>`
  - Create `frontend/components/command/HealthDrawer.tsx` — `role="dialog"` aside panel, slides from right (240ms, `--ease-out`), width 400px; reads `useRuntimeHealth()` query; renders each `HealthCheckResponse` as a row with name, status badge (pass/warn/fail mapped to jade/ember/danger), and detail text; shows loading skeleton while fetching; shows error state with retry if fetch fails; close button and `Escape` key close it
  - Create stub pages for all 7 non-Command-Center routes — each renders `<UnavailablePage name="X" phase="FY" />` with the correct name and phase label from the Phase One doc
  - Write Vitest tests in `tests/components/` for: `Sidebar` renders all 8 nav items; active item detected correctly; approval count badge appears when count > 0; `SystemStatusStrip` shows offline state when status is unavailable; `SystemStatusStrip` shows kill switch state
  - Run `npm run dev` — shell renders at all breakpoints; `npm run test` — all shell tests pass
  - _Requirements: REQ-6.1, REQ-6.2, REQ-2.4, REQ-7_
  - _Demo: Open localhost:3000; sidebar shows ATLAS wordmark + all 8 nav items; status strip shows ATLAS ONLINE or OFFLINE based on whether `just serve` is running; clicking the status strip opens HealthDrawer; resizing to mobile shows bottom nav_


- [ ] 12. Build the Command Center screen: composer, receipt, timeline, and capability posture
  - Create `frontend/features/command-center/queries.ts` with `useRuntimeStatus()`, `useTasks()`, `useApprovals()`, `useCapabilities()` hooks — all using `useQuery` with the appropriate `queryKey` and `queryFn` from `atlasApi`; `useRuntimeStatus` polls every 5s with `retry: false`; `useApprovals` polls every 3s
  - Create `frontend/features/command-center/mutations.ts` with `useCreateTask()` and `useDecideApproval()` exactly as in design doc — `useCreateTask` invalidates tasks and runtimeStatus on success; `useDecideApproval` has NO optimistic update, invalidates approvals and runtimeStatus only after server response
  - Create `frontend/components/command/CommandComposer.tsx` — auto-growing `<textarea>` (min 1 line, max 8 lines via JS); placeholder `"What should ATLAS do?"`; `⌘K`/`Ctrl+K` global keyboard shortcut that calls `.focus()` on the textarea ref (use `useEffect` + `keydown` listener on `window`); `Enter` submits (not `Shift+Enter`, which inserts newline); submit button with `--royal-400` background; disabled with informative text when `runtimeStatus.state !== "ready"` OR `kill_switch_active === true` OR `createTask.isPending`; after successful submit clears the textarea and calls `onTaskCreated(task)`
  - Create `frontend/components/command/IntentReceipt.tsx` — renders below the composer after successful task creation; shows `truncateId(task.id)` in `--font-plex-mono`, source badge, `<TaskStateBadge state={task.state} />`, a message "Approval may be required" in ember if `pendingApprovalCount` increased after submission; auto-hides after 30s via `setTimeout` cleanup; prop `onDismiss` for manual close
  - Create `frontend/components/runtime/TaskStateBadge.tsx` — thin wrapper around `<Badge>` for task-specific states; maps the state machine values to the Badge state vocabulary
  - Create `frontend/components/runtime/ActivityTimeline.tsx` — reads `useQuery` for the most recent task's events (`useTaskEvents(latestTaskId)`) and `useAudit({limit: 10})`; renders a vertical list of timeline entries; each entry: timestamp (right-aligned, `--font-plex-mono` `--text-sm`), colored left border by event type (agent=royal, system=paper-300, error=danger), humanised label, optional tool chip; loading state shows 5 skeleton rows; empty state shows "No activity yet — submit a task above"
  - Create `frontend/components/approvals/ApprovalInbox.tsx` — reads `useApprovals()`; hidden when `data.length === 0`; when visible, renders a section heading "Approval Required" in `--gold-400`; renders `<ApprovalCard>` for each pending approval
  - Create `frontend/components/approvals/ApprovalCard.tsx` — Panel with gold left border; header: capability·operation in mono, `<Badge state="pending" />`; expiry countdown in ember if < 5 min; preview text in a scrollable `<pre>` block (max 300px height, font-size 13px, `--font-plex-mono`), collapsed to 5 lines with "Show full preview" expand toggle; warnings rendered line by line with ⚠ prefix
  - Create `frontend/components/approvals/ApprovalDecisionButtons.tsx` — two buttons: `[Approve once]` (primary style) and `[Deny]` (ghost style with danger text); both disabled while `useDecideApproval.isPending`; buttons call `mutate({id, decision})` from `useDecideApproval` passed as props; label is "Approve once" not "Approve" — enforced in component
  - Create `frontend/components/capabilities/CapabilityPosture.tsx` — reads `useCapabilities()`; renders a compact table with columns: name, `<Badge state={cap.state} />`, provider count string (e.g. `"3 providers"` or `"auth required"`); loading state shows 4 skeleton rows; no interactive controls
  - Wire all components into `frontend/app/page.tsx` as `<CommandCenterView>` (thin wrapper) or directly; layout matches the spec: greeting + posture, then composer, then timeline, then approval inbox, then capability posture
  - Write Vitest tests in `tests/components/` for: `CommandComposer` — renders enabled when ready, disabled when not ready, clears after submit, `⌘K` focuses the textarea; `ApprovalCard` — renders full preview text, collapses long preview, shows expiry countdown; `ApprovalDecisionButtons` — both buttons disabled while pending, label is exactly "Approve once"
  - Run `npm run test` — all new component tests pass; `npm run typecheck` — zero errors
  - _Requirements: REQ-6.1, REQ-6.2, REQ-6.3, REQ-6.4, REQ-6.5, REQ-6.6_
  - _Demo: With both `just serve` and `npm run dev` running — type a task in the composer, press Enter, IntentReceipt appears with a task ID, ActivityTimeline shows events as they stream in, CapabilityPosture shows real capability states from the backend_


- [ ] 13. Add all disconnected, empty, error, and loading states for every data region
  - Audit every component that calls `useQuery` or `useMutation` — `ActivityTimeline`, `ApprovalInbox`, `CapabilityPosture`, `SystemStatusStrip`, `HealthDrawer`, `CommandComposer` — and verify all four states are handled: loading (skeleton), empty (EmptyState), error (ErrorState with retry), disconnected (distinct from error — shows reconnection info)
  - In `ActivityTimeline`: loading → 5 skeleton rows; empty → `<EmptyState icon={Activity} heading="No activity yet" body="Submit a task above to see it here" />`; error → `<ErrorState message={error.message} onRetry={refetch} />`
  - In `ApprovalInbox`: loading → 2 skeleton approval card shapes; empty → renders nothing (section only visible when approvals.length > 0)
  - In `CapabilityPosture`: loading → 4 skeleton rows; empty → `<EmptyState heading="No capabilities registered" body="Check atlas doctor" />`; error → `<ErrorState onRetry={refetch} />`
  - In `HealthDrawer`: loading → skeleton list; error → `<ErrorState message="Health check unavailable" onRetry={refetch} />`
  - In `SystemStatusStrip`: when `useRuntimeStatus` returns error or `isLoading` for more than 3s, transition to "ATLAS OFFLINE" visual state (not to an error component — the strip must always render)
  - In `CommandComposer`: add a "disconnected" disabled state distinct from "not ready" — when `useRuntimeStatus` query has `isError: true`, show `"Cannot reach ATLAS server"` below the textarea rather than `"ATLAS is not ready"`
  - For `app/loading.tsx` (route-level skeleton): render `<AppShell>` skeleton with sidebar skeleton + content area skeleton using `<Skeleton>` primitives
  - For `app/error.tsx` (route-level error boundary): render `<AppShell>` with `<ErrorState heading="Something went wrong" message={error.message} onRetry={reset} />` in the main content area
  - Write Vitest tests for each disconnected state: `SystemStatusStrip` offline rendering, `CommandComposer` disconnected state, `ActivityTimeline` error state + empty state
  - Run `npm run test` — all state tests pass; `npm run typecheck` — zero errors
  - _Requirements: REQ-6.7_
  - _Demo: Stop `just serve`; reload localhost:3000 — status strip shows "ATLAS OFFLINE", composer shows "Cannot reach ATLAS server" and is disabled, timeline shows error state with retry button. Restart `just serve`; the UI recovers automatically within one poll cycle (≤5s)_

- [ ] 14. Add accessibility baseline and run the full check suite
  - Audit all interactive elements for keyboard operation: Tab order follows visual reading order; Enter/Space activate buttons; Escape closes drawers and dialogs; arrow keys work inside any component that needs it
  - Add `aria-live="polite"` to `SystemStatusStrip` and to the approval count in `Sidebar` — screen readers announce status changes without interrupting the user
  - Add `aria-label` to all `<IconButton>` components (icon-only buttons must have accessible names)
  - Confirm all landmark elements are present in `AppShell`: `<header>` wraps `SystemStatusStrip`, `<nav>` wraps `Sidebar`/`MobileNav`, `<main>` wraps content, `<aside>` wraps `HealthDrawer`
  - Confirm `HealthDrawer` has `role="dialog"`, `aria-modal="true"`, `aria-labelledby` pointing to its heading; focus is trapped inside while open; focus returns to the trigger button on close
  - Confirm `ApprovalCard` preview `<pre>` block has `role="region"` and `aria-label="Action preview"`
  - Verify all touch targets are at minimum 44×44px — check the Approve/Deny buttons, nav items, and composer submit button in browser DevTools
  - Verify WCAG AA contrast for all text/background pairs in the OKLCH palette by running `npx contrast-ratio-checker` or checking in browser DevTools Accessibility panel
  - Confirm `prefers-reduced-motion` disables the status dot breathing animation and the HealthDrawer slide-in — test by setting the OS motion preference to "Reduce" in System Preferences and reloading
  - Run `npm run check` (typecheck + lint + test) — everything green
  - _Requirements: REQ-7_
  - _Demo: Keyboard-only operation: Tab through the full page without touching the mouse; every interactive element is reachable, has a visible focus ring, and activates correctly_


- [ ] 15. Write E2E tests, wire to live backend, and verify the Phase One definition of done
  - Install Playwright browsers: `cd frontend && npx playwright install chromium`
  - Write E2E tests in `frontend/tests/e2e/phase-one.spec.ts` covering all scenarios from REQ-8.3:
    - `open with backend unavailable` — stop `just serve`, load `localhost:3000`, assert status strip shows "ATLAS OFFLINE", composer is disabled, timeline shows error state
    - `connect and see runtime status` — start `just serve`, reload, assert status strip shows "ATLAS ONLINE"
    - `submit a task and receive receipt` — type a task into composer, press Enter, assert `IntentReceipt` appears with a task ID in monospace font
    - `approve pending action` — if an approval exists in the pending list, click "Approve once", assert button goes disabled during POST, assert card disappears after server response
    - `deny pending action` — same flow with Deny button
    - `kill switch visible and requires confirmation` — click status strip to open HealthDrawer, verify kill switch state is visible
    - `reconnect after stream disconnect` — use `page.route` to block the SSE endpoint, wait for "reconnecting" badge, unblock, assert status recovers to "connected"
  - Use Playwright `page.route()` to mock the API for all tests that do not require the live backend — create a helper `mockBackend(page)` that intercepts all `/api/v1/*` requests and returns the fixture JSON files
  - Run `npm run test:e2e` against the mock backend — all 7 E2E scenarios pass
  - Run `npm run test:e2e` against the live backend (both `just serve` and `npm run dev` running, Ollama running) — all scenarios pass
  - Final checklist — verify every item in the Phase One definition of done (from doc section 13):
    - [ ] UI runs from a clean `npm install && npm run dev`
    - [ ] UI works against mock backend without changes
    - [ ] UI works against live API contract
    - [ ] No screen contains invented operational data (grep for hardcoded task IDs, provider names, fake approvals)
    - [ ] All mutations use `idempotency_key` (check Network tab)
    - [ ] Approval UI never replaces backend Safety Engine approval (verify Approve button calls POST, not a local state toggle)
    - [ ] Event reconnection and REST reconciliation work (tested in E2E)
    - [ ] Keyboard and reduced-motion pass (from Task 14)
    - [ ] Desktop and mobile visual states covered at 1440px and 390px (screenshot both in Playwright)
    - [ ] Frontend has zero imports of Python modules, provider SDKs, or `atlas/src` paths
  - Commit all changes on branch `frontend/foundation` with message: `feat(ui): ATLAS Phase One — backend API adapter, design system, and Command Center`
  - _Requirements: REQ-8.3, all definition-of-done criteria_
  - _Demo: `npm run check` exits 0. `npm run test:e2e` exits 0. The Command Center is live, connected to the real ATLAS backend, and every approval shows the exact outbound action text._
