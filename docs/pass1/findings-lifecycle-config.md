# Phase 0 Findings — Lifecycle, Workers, Config, Adapters

## P0 — `atlas runtime start` builds and starts TWO complete Atlas instances

Three entrypoints call `build()`; the canonical CLI one calls it **twice**:

1. `atlas_cli/start.py:56` `atlas = await build()` → `:60` `await atlas.start()` ← **instance #1**
2. `start.py:82` `uvicorn.Config(create_app(), ...)` → lifespan → `api/app.py:45-46`
   `atlas = await build()`; `await atlas.start()` ← **instance #2**

Instance #1 is closed **only** inside the `except KeyboardInterrupt` branch (`:98-99`). Any
other exit leaks it. Two instances against one `atlas.db` means: two SQLite connections, two
`MessageBus` queue processors competing over the same `events` rows, two `CronScheduler`s both
firing `memory_consolidation`, two `EmbeddingWorker`s, two `RuntimeSupervisor`s.

Also: `routes_events.py` keeps a **module-level global** `_deps` set by `set_dependencies(...)`
from `create_app`. Two `create_app()` calls in one process leave `_deps` pointing at the
**second** Atlas's db/bus.

`_handle_signal` (`start.py:23-26`) only prints — it sets no event and stops nothing.

## P0 — The RuntimeSupervisor constructs nothing and its health loop is unreachable

`bootstrap/runtime.py`, 785 lines. **Everything is constructed in `build()`.** The supervisor is
a post-hoc assertion pass over an already-built object graph. Verbatim from `_initialize_phase`
(`:250-286`):

- `:256-258` `if phase.name == "bootstrap":` / `# Configuration already loaded, just validate`
- `:260-262` `# Infrastructure already initialized in app.py`
- `:264-286` — the same comment shape for safety / intelligence / memory / capabilities /
  orchestration / readiness (`pass`)

**Phase timeouts are decorative.** `_execute_startup_phase` (`:205-248`) has **no
`asyncio.wait_for` / `asyncio.timeout`**. `phase.timeout_seconds` is read only inside the log
line at `:232`. A hung provider probe in the `intelligence` phase blocks startup forever despite
`timeout_seconds=10.0`.

**The 60 s health loop can never run a health check** (`:634-654`):
```python
await asyncio.wait_for(self._shutdown_event.wait(), timeout=self._health_check_interval)  # :639
if self._shutdown_event.is_set():
    break                          # :644
await self._run_readiness_checks() # :647  ← UNREACHABLE
except TimeoutError:
    continue                       # :651
```
Expiry raises `TimeoutError` → `continue`. The only fall-through is shutdown → `break`.
`get_health_report()` therefore serves whatever `_component_health` was frozen at startup,
forever.

**`_background_tasks` (`:136`) is never written to**, so the cancel loop at `:722-735` iterates
an empty set every time.

**`STARTUP_PHASES`** (`:93-102`): bootstrap(1s,crit), infrastructure(5s,crit), safety(2s,crit),
intelligence(10s,crit), memory(5s,**non-crit**), capabilities(10s,**non-crit**),
orchestration(3s,crit), readiness(2s,crit).

**`SystemState`** (`:31-40`) has all 8 Phase-21 states. **`BUSY` and `RECOVERING` are never
assigned anywhere.**

### Two state-calculation bugs (`_calculate_system_state` :421-451)

1. **`"configuration"` is in `CRITICAL_COMPONENTS` (`:84-90`) but never health-registered.**
   Rule 2 (`:436-441`) filters on *present* keys, so a missing critical component is silently
   treated as fine.
2. **The default config pins the system to `DEGRADED` forever.** `browser` is set `UNAVAILABLE`
   whenever `config.browser.enabled` is false (`:563-566`), which is the default
   (`config.py:126`) — rule 3 then yields `DEGRADED`, never `READY`.
3. Conversely `_check_intelligence_health` (`:506-535`) sets `intelligence_gateway=FAILED` when
   zero providers respond → rule 1 → `FAILED` → `start.py:67-69` exits 1. **Under `local_free`,
   Ollama down ⇒ ATLAS refuses to boot.**

`shutdown()` (`:672-706`) **returns immediately if state is `SHUTTING_DOWN` or `FAILED`**
(`:678-679`) — after a failed startup nothing is stopped. `_stop_accepting_tasks` (`:708-711`)
is a no-op that only logs.

## Worker inventory — 23 `create_task` sites, 7 abandoned

`RUF006` (store-a-reference) is **explicitly disabled** at `pyproject.toml:81`.

**Supervised (task handle + stop):** `MessageBus._process_queue` (`bus.py:89`),
`CronScheduler._run` (`scheduler.py:85`), `EmbeddingWorker._process_queue` (`embedder.py:119`),
`RuntimeSupervisor._health_monitor_loop` (`runtime.py:631`), `EventBroadcaster` /
`MemoryBroadcaster` keepalives (`websocket.py:150,193`).
`_worker_registry` holds exactly **two** entries: `embedding_worker`, `scheduler`.

**Abandoned — no handle, no cancel, no await, GC-collectable mid-flight:**

| # | Site | What it does |
|---|---|---|
| 1 | `api/app.py:51` | startup DB backup; races first requests |
| 2 | **`api/facade.py:171-174`** | **runs the entire orchestrator for every API task** — untracked; shutdown never drains it; in-flight tasks severed |
| 3 | `memory/episodic.py:89` | enqueue embed |
| 4 | `memory/episodic.py:194` | memory WS event |
| 5 | `memory/trajectory_store.py:143` | trajectory WS event |
| 6 | `orchestration/self_critique.py:122` | up to 3 fact-writes per reflection; unbounded fan-out |
| 7 | `orchestration/orchestrator.py:319` | LLM-backed experience extraction — long-running, killed on shutdown |

`bus.close()` cancels `_task` but does not await it → the queue processor can be mid-`execute`
when `db.stop()` runs at `app.py:214`.

**Implemented but never constructed:** `infra/queue.py DurableTaskQueue`,
`orchestration/worker.py TaskWorker` (reachable only from the manual `atlas worker` CLI),
`infra/backends.py PostgresConnection`.

**`CronScheduler.tick()` return value is discarded** (`scheduler.py:103`). DB-backed schedules
update `last_run_ts`/`next_run_ts` and log `scheduler.triggered` but **never dispatch a task**.
`next_run_ts` is set to `now.isoformat()` (`:157-158`) — never computed from the cron expression.

## Phase 28 — PerceptionAdapter / ControlAdapter do not exist

`perception/` 185 lines, `control/` 193 lines. The only `*Adapter` class in `src/` is
`NotificationPlatformAdapter` (`app.py:309-349`), unrelated.

Existing protocols: `PerceptionBackend` (`perception/backend.py:11-13`) — `capture_frontmost()`,
`available()`; **no `snapshot`, no `health`**. `ScriptRunner` (`control/osascript.py:26-27`) —
`run(script, timeout_s)`; **no `dispatch`, no `health`**.

**Both directories are complete, well-designed, fully dead code.** A repo-wide grep for
`AXPerceptionTool|AppControlTool|PerceptionBackend|MacOSAX|OsascriptRunner|ScreenState` outside
those two dirs returns **zero hits**. Neither tool is in the `tools` dict (`app.py:429-441`);
neither has a rule in `permissions.yaml`. `is_sensitive_app()` and `ScreenState.sensitive` are
never read.

## Config — two disjoint pipelines; the documented precedence does not exist

`config.py:5` claims *"code defaults < settings.yaml < environment/.env"*. **No such chain
exists.** `Settings` (`load_settings()`) reads **only** env + `.env`. `AppConfig`
(`load_app_config()`) reads **only** `settings.yaml`. No key can be set in YAML and overridden
by env, or vice versa.

**`settings.yaml` top-level `profile`/`cost_policy`/`network_policy` (`:8,13,14`) are silently
discarded** — `AppConfig` has no such fields and pydantic drops extras at `config.py:166`. The
comment at `settings.yaml:7` *"ATLAS_PROFILE env var overrides this"* is wrong: the YAML value
never has any effect. `ATLAS_PROFILE` is the only source.

**`OPENROUTER_API_KEY`** has no `ATLAS_` prefix — explicit `validation_alias` at `config.py:41`.

### Dead config (declared, never read)

`Settings.default_model`, `.heavy_model`, `.cost_policy`, `.network_policy`;
`ModelCfg.gpu_concurrency`, `.allow_cloud`, `.daily_usd`, `.weekly_usd`, `.monthly_usd`,
`.per_task_usd` (all four budgets shadowed by `profile.*` at `intelligence.py:110-115`);
`SafetyCfg.confirm_timeout_s`; `NotifyCfg.quiet_hours` (and its YAML value is a *list* while the
type is `dict` → setting it raises `ValidationError`); `MetricsCfg.snapshot_interval_s`;
`TracingCfg.enabled`; `BrowserCfg.headless`, `.default_provider`; `StartupPhase.dependencies`;
`permissions.yaml whatsapp.known_contacts`; `notifications.yaml digest_windows`.
**`settings.yaml` has no `memory:` block at all** → all 5 `MemoryCfg` fields are always defaults.

### Read but hardcoded (not configurable)

`_health_check_interval = 60.0`; `shutdown(timeout_seconds=30.0)`; all 8 phase timeouts;
`ExecutionLimits(max_steps=15)`; `EmbeddingWorker(batch_size=10, max_queue_size=1000)`;
scheduler `interval_seconds=60.0`; consolidation cron `"0 2 * * *"`;
`TokenBucketLimiter(capacity=120, refill_per_minute=60)`; CORS `["http://localhost:3000"]`;
WS keepalive 30 s; bus `LIMIT 50`; `CLAIM_LEASE_S = 600.0`; all nine quota numbers
(`intelligence.py:122-130`); port 8730/host 127.0.0.1; `facade.py:81 version="1.0.0"`.

## Secrets — storage, and 8 real leak surfaces

LLM provider keys are **not** in the `SecretStore`. They live in plain env/`.env` as `Settings`
fields, passed as constructor args (`intelligence.py:144,159,171-174,184`).

Redaction machinery exists (`safety/engine.py:20-70`) and is invoked from **exactly one call
site**, `engine.py:293`.

1. **`_SECRET_PATTERN`'s `sk-[A-Za-z0-9]+` does not match an OpenRouter key.** Keys are
   `sk-or-v1-…`; the character class excludes `-`, so redaction preserves `-v1-<secret>`.
2. **Raw provider response logged on empty content** — `openai_compatible.py:116-120`, not
   routed through `_redact_payload`.
3. **Dev master key is deterministic from public info** — `sha256("dev-atlas-" + hostname)`
   (`config.py:190-195`). Anything in the dev `secrets` table is decryptable by anyone who knows
   the hostname.
4. **The bus bypasses redaction entirely** — `bus.py:119-144` persists
   `event.model_dump_json()` into `events` with no filtering.
5. **API task-event metadata is DENYLIST-filtered** (`api/app.py:83-102`) — 12 known keys are
   excluded and **everything else passes through** into `task_events`, SSE, and the frontend. A
   new metadata key carrying a secret is exposed by default. Inverse of fail-closed.
6. **Trajectories are prompt-bearing and HTTP-served** — `GET /api/v1/trajectory/*`. Any secret
   pasted into a task request is retrievable.
7. **Transport auth fails open** — `api/app.py:194-198` wraps `parse_api_keys` in
   `except Exception: app.state.api_keys = {}`, and empty ⇒ **no authentication on any route**.
   A malformed `ATLAS_API_KEYS` silently disables auth rather than refusing to boot.
8. **Repo root (containing `.env`) is bind-mounted read-write into the model-driven shell
   sandbox** — `app.py:428` `ws = str(_REPO_ROOT)`, `:439` `mounts={ws: "/work"}`, while
   `permissions.yaml:8` allowlists `cat`/`grep`/`find` at **Tier 1 (auto-approve)**. A
   prompt-injected `cat /work/.env` is auto-approved.

Also: a real personal email address is committed as `credential_id` in three config files.
`provider.health()` for OpenRouter is `return bool(self._key)` (`openai_compatible.py:197-198`)
— key *presence*, not reachability, so a revoked key still reports healthy.

## Profiles

`infra/profiles.py`. `local_free` (ZERO_COST/LOCAL_ONLY/PRIVATE/no cloud) is the default and the
Dockerfile value.

**Dead profile fields:** `enable_rate_limiting` (the API limiter is unconditional),
`default_privacy` (never read), `cost_policy`/`network_policy` (only logged — no code path
enforces them). `resolve_profile` falls back to `LOCAL_FREE` **silently** on `ValueError`, so
`ATLAS_PROFILE=prod` downgrades to local-only with no warning.

**Profile-violation bug:** `intelligence.py:206-216` gates `sync_openrouter_free_models` on
**key presence only**, not `profile.allow_cloud`. Under `local_free` a present
`OPENROUTER_API_KEY` still makes an **outbound call to openrouter.ai on every startup** and
registers dozens of specs into a registry with no openrouter provider to serve them.

## Sandbox — default posture is unsandboxed

`app.py:393-426`: Docker health probe → if unavailable **and `settings.env == "dev"`** (the
default, `config.py:29`) → `NativeSandbox`, warning only. Otherwise `FatalError`. Under
`NativeSandbox` the `mounts={repo_root: "/work"}` is meaningless and shell commands execute
directly against the host with the process's own privileges. **No config key forces Docker in
dev or forbids native.**

## Other contradictions

- `bootstrap/intelligence.py:71` accepts `bus` — **`app.py:259-267` never passes it.** So
  `InferenceRuntime._bus is None` and `_emit_event` returns early (`inference.py:194`). **Every
  `provider.rate_limited` and `provider.failed` event is silently dropped**, invisible to the
  bus, the WS broadcasters, and the frontend.
- `runtime.py:1-12` claims "Component health monitoring / Background worker management /
  Failure recovery" — monitoring never runs, worker management is 2 workers, and `RECOVERING` is
  never assigned.
- `bootstrap/capabilities.py` reads per-capability YAMLs behind bare `except Exception:` — a
  typo in `email.yaml`/`calendar.yaml`/`contacts.yaml` degrades silently.
- `api/health.py`'s `None` fallback reports READY from a DB check alone, hiding the supervisor's
  real `DEGRADED`/`FAILED` state from probes.
- `atlas_cli/start.py` `stop`/`restart` are TODO stubs; `--daemon` exits 1.
- `_verify_build.py` at repo root is a build→start→close smoke script, not in the test suite.
