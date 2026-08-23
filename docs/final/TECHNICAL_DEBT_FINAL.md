# TECHNICAL_DEBT_FINAL — ATLAS (verified)

Defects and gaps found by reading the code. Severity: 🔴 breaks a real user path ·
🟠 truth/consistency violation · 🟡 quality/maintainability.

Two sections:

- **Part 1** is the original Phase-0 audit, now carrying a **Status** column. Rows marked
  ✅ were re-verified as closed by reading the file named in the row — not assumed from a
  changelog. Rows marked ⬜ are still open.
- **Part 2** is what the robustness pass found and **deliberately did not fix**, with the
  reason. It exists so that "the gates are green" is never mistaken for "there is nothing
  left"; several items here are risks the test suite cannot see.

---

## Part 1 — Phase-0 audit

### Frontend — truth / Zero-Dead-UI violations

| # | Sev | Location | Problem | Status |
|---|---|---|---|---|
| 1 | 🔴 | `app/dashboard/page.tsx` | Raw `fetch` to **`:8000`** (wrong port), `any` types — legacy prototype, effectively broken | ✅ Retired: the file is now an 8-line `redirect("/")`; metrics live on Home |
| 2 | 🟠 | `components/layout/Topbar.tsx` | Hardcoded **"Telegram bridge connected"**; health button `alert()`-only; **kill-switch button only `alert()`s — no backend call** | ✅ Status pill derives from `runtimeStatus()`; real health popover; kill switch is a read-only indicator (no HTTP trip endpoint exists, so none is faked); telegram text gone |
| 3 | 🟠 | `components/dashboard/HeroSection.tsx` | `runtimeText="idle"` hardcoded; model **"GLM-5.2 (Remote)"** hardcoded; `const spend = 0.18 // Mock` with a progress bar | ✅ All three gone; state derives from `runtimeStatus()`/`runtimeHealth()`, and counts render `—` before the first response rather than `0` |
| 4 | 🔴 | `components/dashboard/ApprovalInbox.tsx` | Approve/Deny `<button>`s have **no onClick** (dead) | ✅ Wired to `atlasApi.decideApproval` with an idempotency key; failures render via `ErrorRow` |
| 5 | 🟠 | `components/workspace/CommandWorkspace.tsx` | mic/camera/screen toggles are UI-only stubs; fake **1500ms "analyzing"** `setTimeout` | ⬜ **Partial** — the fake delay and the fabricated pre-flight are gone (Part 2 · #21); the three input toggles are still stubs (Part 2 · #14) |

### Frontend — architecture / consistency

| # | Sev | Location | Problem | Status |
|---|---|---|---|---|
| 6 | 🔴 | `lib/api/client.ts` `trajectoryApi.experiences` | Calls `/api/v1/trajectory/experiences` while `API_BASE` already ends `/api/v1` → **double prefix → 404** | ✅ Fixed; the leading prefix is gone and a comment records why |
| 7 | 🟠 | `lib/api/client.ts` | Two client styles: typed `request()` (zod) vs untyped `requestJSON()`. `trust/autonomy/learning/ops/providers` bypass validation | ⬜ **Partial** — `trust` and `memory` now validate through `parseContract`; `autonomy`/`learning`/`ops`/`providers` still `as`-cast (Part 2 · #13) |
| 8 | 🟠 | `components/layout/Sidebar.tsx` | IA differs from Pass-5 target; owner "Aman" hardcoded | ⬜ Open — the owner name is still hardcoded (also in `HeroSection`'s greeting) |
| 9 | 🟡 | frontend routes | No Research / Workspaces / Knowledge / Learning-Lab / System routes exist | ⬜ Open by decision — out of scope; nav items stay disabled-with-reason |

### Backend / cross-cutting

| # | Sev | Location | Problem | Status |
|---|---|---|---|---|
| 10 | 🔴 | `interfaces/api/app.py` CORS | `allow_methods=["GET","POST"]` blocks the **PUT/DELETE** automation routes → browser can't update/delete automations | ✅ `PUT`/`DELETE` added, origin still locked to `localhost:3000`; pinned by a preflight test in `tests/api/test_error_envelope_cors.py` |
| 11 | 🟡 | runtime `state` enum | UI cannot show `BUSY`/`WAITING_APPROVAL` from `state` (enum is starting/ready/degraded/stopping/stopped) | ⬜ Open by decision — the frontend **derives** busy/waiting from real counts and never fabricates `RECOVERING` |
| 12 | 🟡 | stubs | ~19 `NotImplementedError` / ~7 `TODO` across the tree (mostly experimental learning/perception) | ⬜ Open — tracked per subsystem, kept marked EXPERIMENTAL in RELEASE_READINESS |

---

## Part 2 — Known and deliberately not fixed

### Correctness risks the tests cannot see

**13 · 🟠 Four API surfaces still return unvalidated data.**
`autonomyApi`, `learningApi`, `opsApi` and `providersApi` in `frontend/lib/api/client.ts`
end in `as Promise<T>` — a compile-time assertion with no runtime check. If the backend
changes a field, the failure is a `TypeError` deep in a render rather than a contract error
at the boundary. **Not fixed** because it needs a zod schema per endpoint (~20 schemas),
which is a larger change than this pass, and the blast radius is a render crash now caught
by `app/error.tsx` rather than a white screen. Do this next; `features/trust/queries.ts` is
the pattern to copy.

**14 · 🟠 `CommandWorkspace` mic / camera / screen toggles are stubs.**
They set local boolean state and change a border colour. The mic additionally inserts the
literal text `[Voice input recording...]` into the prompt. Nothing is captured, and nothing
is sent. **Not fixed** because implementing capture is a feature, not a hardening task —
but note this is a live Zero-Dead-UI violation: per that rule they should be
disabled-with-reason or removed.

**15 · 🟡 `selectedModel` is write-only.** The footer displays `state.selectedModel`, which
is initialised to `"auto"` and never changed — no model picker dispatches `SET_MODEL`.
"AUTO" is truthful (ATLAS does route automatically), so it is not a lie, but the state
member implies a control that does not exist.

**16 · 🟡 The context-size chip is a heuristic.** `characters ÷ 4` shown as `~N tks`. Real
tokenisation is not exposed to the frontend. Kept because it is labelled `~` and the
tooltip states the formula; replace it if a token-count endpoint ever lands.

### Approvals (documented Phase-3 deferral)

**17 · 🟠 `pending_approval_count` can under-report, and the UI trusts it.**
There is no approval storage, so `pending_approvals()` returns `[]` and the count is
computed from task rows. A task that is genuinely blocked awaiting approval may therefore
not be counted, which means **"Approvals waiting: 0" is not proof that nothing is
waiting**. Not fixed: it needs an approvals table, which is a Phase-3 scope item.
Recording it here rather than papering over it, because the number is one an operator acts
on.

**18 · 🔴 `decide_approval` is not implemented.** The endpoint and the frontend mutation
both exist and are wired; the control-plane method behind it is not. The failure is now at
least *visible* — `ApprovalInbox` renders the error through `ErrorRow` instead of resetting
the button silently — but Approve/Deny cannot succeed until the storage in #17 lands.

### Durability / operations

**19 · 🟠 The startup backup is not crash-consistent.** `infra/backup.py` zips a live
WAL-mode SQLite database. Copying `.db` while `.db-wal` is still being written can capture a
torn state, so a restored archive may be unusable. Retention is now bounded (keep N, default
5, with a cooldown), but the consistency problem is **unchanged** and the docstring says so.
The correct fix is `Database.backup_to()` / `VACUUM INTO`, which serialises against writers —
tracked, not done, because it changes the backup format.

**20 · 🟠 WebSocket routes carry no authentication.**
`require_principal` takes a `Request`, which does not resolve for a WebSocket scope, so
`memory_router` and `events_ws_router` are included **without** the auth dependency while
every HTTP router has it. With `ATLAS_API_KEYS` set, HTTP is enforced and WebSockets are
still open. **Not faked** — a `Depends` that silently no-ops on a WebSocket would be worse
than the honest gap. Needs a subprotocol- or query-token handshake.

### Cleaned in this pass — recorded so the reasoning is not lost

**21 · 🟠 A fabricated safety clearance on the authorisation gate.**
`components/workspace/preflight/PlannerPreview.tsx` rendered "✓ Policy OK" and
"⚠ Needs Approval (Tool)", "Models Routed: GPT-5 (Reasoning) / GLM-4V (Vision)",
"Est. Runtime 15-45s", "Est. Cost $0.002", a "DAG Preview" badge and a
`[Vision: OK] [Policy: SC-42] [Context: Build]` strip — none of it computed, behind a
`setTimeout(1500)` captioned "Analyzing request & classifying safety…". There is no
pre-flight endpoint; the safety engine classifies *after* this POST. **Removed.** Logged
because it is the sharpest example of the failure mode this project exists to avoid: a
consent screen asserting a check that never ran.

**22 · 🟠 A fabricated safety tier in the activity feed.**
`components/dashboard/ActivityTimeline.tsx` held `["AUTO","CONFIRM","BLOCK","AUTO","AUTO"]`
indexed by row position and rendered it as a badge. `TaskSchema` carries no tier field —
only `TaskEventSchema` does — so every label was invented and the third row always claimed
"BLOCK". **Removed**, replaced with the real `task.source`.

**23 · 🟡 A dead "Enabled" checkbox.**
`app/capabilities/page.tsx` renders `<input type="checkbox" defaultChecked={cap.state === 'ready'}>`
with **no `onChange`**, so it toggles visually and changes nothing. There is no
enable/disable capability endpoint to wire it to. ⬜ **Still open** — it needs either a
backend endpoint or removal; left in place rather than silently deleting a control someone
may be depending on visually. Flagged for the next pass.

**24 · 🟠 Three hardcoded posture cards.**
`components/dashboard/CapabilityPosture.tsx` still hardcodes "Knowledge: official sources
ready", "Email: approval path active", and "Browser: planned, not enabled / phase 6.7". The
*Intelligence* card was fixed (it now distinguishes "status unavailable" from "0 ready", so
a failed request no longer asserts a measurement). ⬜ **The other three are still literals** —
they need per-capability health from `/capabilities`, which does not yet model it.

**25 · 🟡 `app/events/search/page.tsx` is a prototype.**
Light-theme styling inconsistent with the rest of the app, a raw `fetch` bypassing the typed
client, `throw new Error('Search failed')`, and `response.json()` used unvalidated. It has
an error branch, so it is not a crash risk — but it is the one page not on the shared
client. ⬜ Open.

### Gate hygiene

**26 · 🟡 `just check` is stricter than CI.**
`just lint` runs `ruff check` **and** `ruff format --check`; `.github/workflows/ci.yml`
runs only `ruff check`. **94 files fail `ruff format --check`** on a pre-existing basis, so
`just check` cannot go green until either CI adopts the format gate and the tree is
reformatted, or the local target drops it. Deliberately **not** resolved in this pass: a
94-file formatting-only diff would bury the substantive changes under review. Pick one and
make the two agree.

**27 · 🟡 `ToolMetadata.safety_tool` is never populated.** The field exists and is read when
deriving capability state, but nothing writes it, so that derivation has a permanent
`None` input. Harmless today; a trap for whoever relies on it next.

**28 · 🟡 `npm audit` reports 5 high-severity advisories** in the frontend dependency tree,
surfaced when vitest was added. None are in a runtime path this app exercises, and the fixes
are major-version bumps of build tooling. Not taken during a hardening pass whose point is to
avoid unrelated churn — but they are real and should be triaged on their own.

**29 · 🟡 The type and coverage gates still cover only `atlas`.**
`[tool.mypy] packages = ["atlas"]` excludes `src/atlas_cli` and `tests/`, and
`[tool.coverage.run] source = ["atlas"]` excludes `atlas_cli` — so the CLI package that
`[project.scripts]` ships as the `atlas` entry point is neither type-checked nor measured.
Widening both was planned for this pass and **deliberately not applied**, because neither
change can be made responsibly without running the tool: turning on `strict` for two
previously unchecked trees produces an error backlog that has to be *fixed* (the alternative,
a blanket `ignore_errors`, is a gate that lies), and adding `atlas_cli` to the coverage source
lowers the measured total, which means `fail_under = 63` has to be re-set to whatever the run
actually reports rather than guessed at. Applying either blind would leave a red gate in the
tree with no way to confirm the fix. Do this as its own change, with the tools in front of you.

**30 · 🟡 A stray diagnostic script at the repo root.**
`test_app_routes.py` is a `print`-based script with no assertions that pytest never runs
(`testpaths = ["tests"]`). Its checks — which routers register, which memory/trajectory paths
exist — are covered properly by `tests/api/test_ws_contract.py`, which asserts on
`app.routes`. It should be deleted; it survives only because it is named like a test and so
reads as one.
