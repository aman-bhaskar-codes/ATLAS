# FINAL_ARCHITECTURE — ATLAS (verified)

> Pass-5 Phase 0. Every claim below was verified by reading the source, not the
> prompt narrative. Status legend: ✅ Stable · ⚠️ Partial/Degraded · 🧪 Experimental · 🛠 Planned/Absent.

## Composition root

`atlas.app.build()` (`src/atlas/app.py`, ~617 lines) returns a frozen-ish `Atlas`
dataclass — the single object graph read by every entrypoint (CLI, API, tests). It
delegates to bootstrap modules: `build_infrastructure`, `build_safety`,
`build_intelligence`, `build_memory`, `build_identity_platform`,
`build_notification_platform`, `build_data_platforms`, `build_knowledge_fabric`
(guarded try/except → `None` on failure), `build_computer_use`, `build_orchestration`.

`Atlas.start()` runs lifecycle → event bus → `RuntimeSupervisor.start()` →
`recover_interrupted_tasks()` (crash recovery). Optional subsystems are typed
`Any = None` and only present when their phase/config enabled them — so *existing in
the graph ≠ active*. Confirmed present & wired: safety engine, memory stack
(episodic/semantic/working/user + trajectory/skill/strategy/world-state), capabilities
(browser, computer-use, public-api, identity, notification, email/calendar/contacts/
weather/location/currency, MCP), knowledge fabric, orchestration + checkpoints.

## Backend module map (`src/atlas/`)

| Area | Modules | Status |
|---|---|---|
| Runtime/orchestration | `orchestration/` (managers, events, router), `agents/`, `autonomy/` | ✅ |
| Safety | `safety/` (engine, classifier, killswitch, audit, sandbox) | ✅ |
| Intelligence | `intelligence/` (gateway, providers, registry, selection, runtime, governance) | ✅ |
| Memory | `memory/` (episodic, semantic, working, user, retriever, consolidator, pruner) | ✅ |
| Capabilities | `capabilities/` (browser, computer_use, public_api, identity, notification, providers/*, pim, platforms) | ⚠️ opt-in per config |
| Knowledge/RAG | `knowledge/`, `capabilities/browser/research` | 🧪 |
| Learning | `adaptation/`, `evaluation/`, `training/`, trajectory/skill/strategy stores | 🧪 |
| Perception/Control | `perception/`, `control/` | ⚠️ |
| Infra | `infra/` (bus, db, migrations, backup, config), `diagnostics/`, `observability/` | ✅ |
| API | `interfaces/api/` (see API_SURFACE.md) | ✅ |
| CLI | `atlas_cli/` (entry `atlas = atlas_cli.main:app`) | ✅ |

## API layer

`interfaces/api/app.py::create_app()` — FastAPI factory (used with `uvicorn --factory`).
Lifespan builds+starts Atlas once, wires the event bus → `TaskEventStore`
(`task_events` table) for SSE, starts WS broadcasters, registers ~17 routers. Runs on
**:8730**. OpenAPI at `/api/docs`. See `API_SURFACE.md`.

## Frontend (`frontend/`)

Next.js 16.2.11 + React 19 (App Router). Root layout mounts `Sidebar`/`Topbar`/
`MobileNav` + a react-query `Providers`. Typed client `lib/api/client.ts` + zod
`lib/api/contracts.ts`. **Two eras**: a modern, typed, dark runtime-console layer
(`app/tasks/[task_id]`, `features/runtime-console/*`, `components/runtime/*`) and a
legacy prototype layer (`app/dashboard` — raw fetch, wrong port `:8000`). See
`FINAL_FRONTEND_ARCHITECTURE.md`.

## Data stores

SQLite via `aiosqlite` (tasks, task_events, audit chain, memory, idempotency —
migrations under `infra/migrations/`, through `007_idempotency_keys.sql`); ChromaDB for
vector memory/knowledge. Startup backup fired async in the API lifespan.
