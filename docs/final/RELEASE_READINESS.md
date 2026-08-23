# RELEASE_READINESS — ATLAS (verified)

Per-capability status from the Phase-0 audit. Definitions:
- **STABLE** — real backend + real UI path, works end-to-end.
- **DEGRADED** — real backend, but a UI bug / fake / missing wiring blocks the full path.
- **EXPERIMENTAL** — real code, unproven / stubbed in places / not primary-path.
- **UNAVAILABLE** — not built (route/feature absent).

## Capability matrix

| Capability | Backend | Frontend | Status | Note |
|---|---|---|---|---|
| Task runtime (create → OTAR → stream) | ✅ | ✅ `LiveRunPage` + SSE | **STABLE** | Core loop, reconciling SSE |
| Safety funnel + tiers | ✅ | n/a (enforced server-side) | **STABLE** | Reference monitor, non-bypassable |
| Audit chain + `/audit/verify` | ✅ | ⚠️ read via trustApi | **STABLE** (backend) | Hash-chained, verifiable |
| Kill switch | ✅ CLI/file | ⚠️ indicator only | **STABLE** (by design) | No HTTP trip; UI must show, not fake |
| Approvals decide | ✅ | ⚠️ real in `ApprovalCard`; **dead in `ApprovalInbox`** | **DEGRADED** | Fix dead buttons (debt #4) |
| Runtime status / health | ✅ `/runtime/*` | ⚠️ not yet on Home/Topbar | **DEGRADED** | Spine B2/B4 wires it truthfully |
| Capabilities posture | ✅ | ✅ | **STABLE** | |
| Memory (search / fact / correct) | ✅ | ✅ typed features | **STABLE** (API) | Fact table not exposed unless requested |
| Providers / cost | ✅ `/providers/*`,`/ops/*` | ⚠️ untyped | **STABLE** | `local_free` → $0.00 |
| Automations | ✅ CRUD | 🔴 **CORS blocks PUT/DELETE** | **DEGRADED** | Fix debt #10 → STABLE |
| Learning (skills/strategies/analytics) | ⚠️ | ⚠️ untyped | **EXPERIMENTAL** | Real routes, unproven |
| Trajectory / experiences | ⚠️ | 🔴 **client double-prefix 404** | **DEGRADED** | Fix debt #6 |
| Knowledge / RAG | 🧪 | UNAVAILABLE (no route) | **EXPERIMENTAL** | Backend present; no UI |
| Browser capability | ✅ (opt-in) | via runtime | **EXPERIMENTAL** | **Off by default** in `local_free` → runtime reports DEGRADED honestly |
| Computer-use / perception / control | 🧪 | UNAVAILABLE | **EXPERIMENTAL** | Some `NotImplementedError` |
| Research / Workspaces / Learning-Lab / System UI | — | UNAVAILABLE | **UNAVAILABLE** | Target IA; nav disabled-with-reason this pass |
| Legacy dashboard | — | 🔴 `:8000` | **DEGRADED** | Retire/migrate (debt #1) |

## Why a fresh runtime says DEGRADED (and that's correct)

Default profile `local_free` disables the browser capability. `runtime/health` therefore
reports a degraded overall with a specific disabled check — this is **truthful capability
reporting, not a fault**. The UI must distinguish "intentionally disabled" from "broken".

## Gate status (to be re-run at end of this slice)

`uv run ruff check .` · `uv run mypy` · `uv run pytest` · `uv run lint-imports` ·
`cd frontend && npm run lint && npm run build`. Recorded here after the Command Center
spine lands. Do not disable architecture rules to pass.

## Readiness verdict

**Local single-user daily driver: usable now for the core loop** (create task → live
trace → approvals → audit). Blocking truth/wiring fixes before it *feels* like one product:
runtime status on Home/Topbar (spine), dead approval buttons (#4), automations CORS (#10),
trajectory 404 (#6), legacy `:8000` page (#1). No "production-ready" / "AGI" / "world-first"
claims — this is a local, safety-governed, single-user system with honest degradation.
