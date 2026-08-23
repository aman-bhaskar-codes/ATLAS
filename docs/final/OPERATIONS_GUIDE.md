# OPERATIONS_GUIDE — ATLAS (verified)

How to run, drive, and operate ATLAS. All commands below are confirmed present in
`src/atlas_cli/main.py` (entry point `atlas = "atlas_cli.main:app"`). Commands referenced
by the Pass-5 prompt that **do not exist** are called out so nobody scripts against them.

## Backend

```bash
uv run uvicorn atlas.interfaces.api.app:create_app --factory --port 8730
```
Serves the API on **:8730**, OpenAPI at `/api/docs`. The lifespan builds+starts the Atlas
object graph once, wires the event bus to SSE/WS, runs crash recovery, and fires a startup
backup asynchronously.

## Frontend

```bash
cd frontend
npm run dev     # dev server on :3000 (CORS allow-list origin)
npm run lint
npm run build   # Next 16 production build
```
Set `NEXT_PUBLIC_ATLAS_API_URL` to override the API base (default
`http://localhost:8730/api/v1`).

## CLI (`atlas ...`) — verified command set

**Top-level:** `run`, `task`, `shell`, `events`, `doctor`, `profile`, `smoke-test`.
**Sub-apps:**
- `atlas runtime` → `start | stop | restart | status`
- `atlas providers` → `list | free | health | verify | sync-openrouter`
- `atlas automations` → `list | create | toggle`
- `atlas cost` → `show | enforce`
- `atlas memory` → `consolidate | promote`
- `atlas models` → `list | doctor`

Useful operator commands: `atlas doctor` (environment/health preflight), `atlas smoke-test`
(end-to-end sanity), `atlas profile` (show/switch deployment profile), `atlas runtime status`
(runtime state from the same source the UI uses).

### ⚠️ Commands the Pass-5 prompt names that DO NOT exist
`atlas benchmark`, `atlas learn doctor`, and a standalone `atlas verify` are **not**
implemented. The closest real equivalents: audit-chain verification is
`GET /api/v1/audit/verify` (HTTP, not CLI); provider verification is `atlas providers
verify`; model diagnostics is `atlas models doctor`. Do not document or script the
nonexistent commands.

## Deployment profiles (`config/settings.yaml`)

Real profile names (not the prompt's LOCAL_MINIMAL/… labels):
`local_free | free_hybrid | free_demo | production`. **Default: `local_free`** with
`allow_cloud: false`. Switch via `atlas profile`. In `local_free` the browser capability
is off by default — which is why a fresh runtime legitimately reports **DEGRADED** (a
capability is intentionally disabled, not broken).

## Data & recovery

- SQLite (tasks, events, audit, memory, idempotency) + ChromaDB (vectors).
- Migrations under `src/atlas/infra/migrations/` (through `007_idempotency_keys.sql`).
- Startup backup runs automatically; crash-interrupted tasks are reconciled on start.

## Access / safety notes for operators

- Local-open API on loopback by default (API keys optional). Anything on localhost can
  drive it — keep it bound to loopback.
- The **kill switch** is CLI/file controlled (no HTTP trip) — to halt the agent, use the
  CLI/file mechanism, not the web UI (the UI only *shows* kill-switch state).

## Quality gates (run before merge)

```bash
uv run ruff check .
uv run mypy
uv run pytest
uv run lint-imports --config importlinter.ini
cd frontend && npm run lint && npm run build
```
Do not disable architecture (import-linter) rules to make them pass.
