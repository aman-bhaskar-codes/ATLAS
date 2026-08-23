# CURRENT_OBSERVABILITY — what ATLAS can already see

**Method.** Every claim below was produced by reading the file named in it during this pass.
Nothing here is recalled from a previous session or inferred from a doc. Where a component
exists but is *not called*, that is stated explicitly — an unused class is not observability.

Legend: ✅ working and wired · ⚠️ exists but partial · 🪦 exists with **zero call sites** ·
❌ absent.

---

## 1. The seven signal kinds (spec §6) as they stand today

| Kind | Where it lives | State |
|---|---|---|
| LOGS | `infra/logging.py` — structlog, JSON in prod | ✅ |
| METRICS | `infra/metrics.py` — `Metrics` | 🪦 constructed, never called |
| TRACES | `infra/tracing.py` — `Tracer.span` | 🪦 constructed, never called |
| EVENTS | `infra/bus.py` — `MessageBus` + `events` table | ✅ durable |
| TRAJECTORIES | `orchestration` + `trajectories` table | ✅ |
| EVALUATIONS | `evaluation/` + `evaluation_results` table | ✅ (see `CURRENT_EVALUATION.md`) |
| INCIDENTS | — | ❌ **no model, no table, no store** |

The spec's point that these "are related but not interchangeable" is already half-honoured:
logs, events, trajectories and evaluations are genuinely distinct stores. Metrics and traces
are the two that exist only as types.

---

## 2. LOGS — ✅ working

`src/atlas/infra/logging.py` (53 lines):

- `configure_logging(cfg: LoggingCfg)` — idempotent, called once at startup.
- Processor chain: `merge_contextvars` → `add_log_level` → `TimeStamper(fmt="iso", utc=True)`
  → `StackInfoRenderer` → (`EventRenamer("message")` + `JSONRenderer`) when `cfg.format ==
  "json"`, else `ConsoleRenderer`.
- `get_logger(module)` binds `module=`.
- `bind_context(**kv: str)` / `clear_context()` wrap `structlog.contextvars`.

**Why this matters for §7 (correlation).** `merge_contextvars` is first in the chain, so
anything bound with `bind_context` rides on *every* subsequent log line in that async
context without being threaded through call signatures. The docstring names the intent
verbatim: "correlation_id/task_id must ride along on every log line". This is the correct
substrate for the §7 correlation keys — the mechanism exists, the key set is just narrower
than §7 asks for.

**Convention observed in the tree:** call sites pass `event_type=` as a discriminator, e.g.
`_log.info("db.ready", event_type="db", …)` (`infra/db.py:1345`) and
`_log.debug("span", event_type="span", …)` (`infra/tracing.py`). Any new engineering-layer
logging must follow it.

**Constraint already satisfied:** the type signature is `bind_context(**kv: str)` — strings
only, so no object graph can be dumped into log context by accident. Combined with
`SafetyEngine._redact_payload` (see `CURRENT_SECURITY.md`) this is the base for §55's
"do NOT dump entire logs blindly".

---

## 3. METRICS — 🪦 dead infrastructure

`src/atlas/infra/metrics.py` (39 lines). Full surface:

```
class Metrics:
    counter(name, value=1, **labels)
    gauge(name, value, **labels)
    observe(name, value, **labels)   # histogram-ish; keeps a list
    snapshot() -> dict
```

All mutation is under a `threading.Lock`. The docstring is honest about scope: "WHY
hand-rolled and not prometheus_client yet: Phase 1 has no scrape endpoint".

**Finding (verified by grep over `src/` for `metrics.counter|metrics.gauge|metrics.observe`):
zero call sites.** The object is constructed in `bootstrap/infrastructure.py` and threaded
into `app.py` and `bootstrap/runtime.py`, and then nothing ever records anything into it.
`snapshot()` therefore always returns empty.

**Consequence for the spec.** §11 (anomaly detection from rolling baselines), §12 (baselines
per task class / capability / provider / model / tool / workflow / strategy) and §42 (event
pipeline health: publish rate, delivery latency, consumer lag) all assume a metric stream
exists. It does not. Either the engineering layer computes its baselines from the SQL tables
directly (`llm_calls`, `events`, `tasks`, `trajectories` — all of which *do* carry
timestamps), or `Metrics` gets real call sites first. **Recommendation: read baselines from
SQL** — the tables are durable and survive restart, whereas `Metrics` is in-process and
resets on every boot, which would make a "rolling baseline" reset with it. Give `Metrics`
call sites for hot-path counters only, and never make an incident depend on it.

---

## 4. TRACES — 🪦 dead infrastructure

`src/atlas/infra/tracing.py` (30 lines):

```
class Tracer:
    def __init__(self, cfg: TracingCfg)
    @asynccontextmanager
    async def span(self, name: str, **attrs: str)
```

`span` measures wall time and emits one `_log.debug("span", event_type="span", span=name,
duration_ms=dur_ms, **attrs)`. There is no span *store*, no parent/child linkage, and no
span id — it is a timing log line, not a trace.

**Finding: zero call sites in `src/`.** Nothing is ever timed.

**Consequence for §74 (trace explorer).** There is currently no data a trace explorer could
render. A trace explorer built on `Tracer` as it stands would show nothing. What *does* exist
and is genuinely trace-shaped is the trajectory + step record (`trajectories`,
`decision_traces`) and `llm_calls` keyed by `task_id`/`step_index`. §74 should be built on
those, and `Tracer` should either gain a persistent span sink or be documented as a debug
timing helper — not presented in the UI as tracing.

---

## 5. EVENTS — ✅ durable, and the strongest existing signal

`src/atlas/infra/bus.py` (231 lines). `MessageBus` is a durable outbox over the `events`
table, not a fire-and-forget pub/sub:

- Columns relevant to health: `durability`, `delivery_status`, `attempt_count`,
  `next_retry_at`, `dead_letter_reason`, `deduplication_key`.
- `_process_queue()` polls `delivery_status = 'pending'` with `LIMIT 50`.
- `subscribe(topic, handler)`, `subscribe_global(handler)`, `register_type(topic, model)`.
- A payload that fails to deserialize is **dead-lettered**, not dropped silently.
- `Event(BaseModel, frozen=True)` already carries `correlation_id`.
- `Topic` constants present: `SYSTEM_STARTUP`, `SYSTEM_SHUTDOWN`, `TASK_CREATED`,
  `SAFETY_CLASSIFY`, `SAFETY_DECISION`, `SAFETY_CONFIRM_REQUESTED`,
  `SAFETY_CONFIRM_RESOLVED`, `CONTROL_KILL`, `MODEL_ROUTE`, `MODEL_CALL`.

**Why this is the best available detector input.** Every field §42 asks for is already a
column: publish rate = `COUNT(*) GROUP BY` time bucket; delivery latency = delivered_ts −
created_ts; consumer lag = `COUNT(*) WHERE delivery_status='pending'`; duplicates =
`deduplication_key` collisions; dead letters = `dead_letter_reason IS NOT NULL`; retries =
`attempt_count`. §42 needs **queries, not new instrumentation**.

`subscribe_global` is also how the engineering layer should ingest signals without touching
producers — `interfaces/api/app.py` already uses exactly this pattern twice (`TriggerEngine`
and `_on_orchestrator_event`).

---

## 6. LLM observability — ⚠️ recorded, not analysed

`src/atlas/infra/llm_tracker.py` (95 lines). `LLMCallTracker`:

```
record(*, task_id, step_index, provider, model, tokens_in, tokens_out,
       cost_usd, latency_ms, cached)      -> INSERT INTO llm_calls
cost_by_model() / cost_by_task() / recent() / cache_hit_rate()
```

`src/atlas/intelligence/observability/telemetry.py` (27 lines) is a thin second surface:
`Telemetry(audit_cost: AuditCostHook)` with `record_success` / `record_failure`, where
`AuditCostHook = Callable[[str, str, str, Usage, int], Awaitable[None]]`.

**Against §8 (LLM observability through the existing ModelGateway).** The per-call facts
§8 wants — provider, model, tokens, cost, latency, cache hit — are all already persisted
per call, keyed to `task_id` and `step_index`. What is missing is the *derived* layer: no
rolling per-model latency/error baseline, no drift comparison, nothing that turns a bad
window into an incident. §10 (model regression detection) is therefore a pure read-side
addition over `llm_calls` — no gateway change needed.

**Against §9 (quality telemetry must distinguish measured / estimated / heuristic).** Today
nothing carries that distinction. `cost_usd` is computed from a price table (estimated),
`tokens_in/out` come from provider usage when present (measured), and the frontend's context
chip is `chars ÷ 4` (heuristic, and correctly labelled `~` — see
`components/workspace/CommandFooter.tsx`). The tri-state exists informally in the UI and
**not at all** in the data model. This is a real gap, tracked in `GAPS.md`.

---

## 7. Provider health — ✅ working, in-memory

`src/atlas/intelligence/health/health_monitor.py` (46 lines): per-provider rolling `deque`
plus a `CircuitBreaker`. Surface: `record(provider, ok, latency_ms)`, `is_available`,
`reliability`, `snapshot`.

This is the one place a real rolling window already exists. It is **in-process** — it resets
on restart and is invisible to any other process. Good enough for routing decisions; not a
durable baseline for §12.

---

## 8. Runtime health — ✅ working, and the natural detector spine

`src/atlas/bootstrap/runtime.py` (786 lines). `RuntimeSupervisor` already models most of
what §43/§44 want:

- `SystemState`: `BOOTING, INITIALIZING, DEGRADED, READY, BUSY, RECOVERING, SHUTTING_DOWN,
  FAILED`.
- `ComponentStatus`: `HEALTHY, DEGRADED, UNAVAILABLE, FAILED`.
- `ComponentHealth(name, status, latency_ms, last_success, last_failure, detail, metadata)`.
- `HealthReport(overall_status, timestamp, components, degraded_components,
  unavailable_capabilities, uptime_seconds)`.
- `CRITICAL_COMPONENTS = {database, safety, orchestrator, configuration,
  intelligence_gateway}`.
- 8 `STARTUP_PHASES`; periodic re-check at `_health_check_interval = 60.0`.
- `get_health_report()`, `get_degraded_components()`, `get_unavailable_capabilities()`.

`ComponentHealth` already has `last_success` and `last_failure`. A health check that
transitions `HEALTHY → FAILED` is exactly a §5 incident source, and nothing currently
listens for that transition.

**The §43 gap is specific and narrow.** `_worker_registry` holds only two entries —
`embedding_worker` and `scheduler` — and tracks **no heartbeat, no last-success timestamp,
and no restart count**. §43 asks for all three plus queue lag and a crash-loop incident.
The registry is the right place to add them.

---

## 9. Request-level correlation — ✅ working at the HTTP edge

`src/atlas/interfaces/api/app.py` (421 lines), the single `@app.middleware("http")
_request_middleware`: accepts an inbound `X-Request-ID` or mints one, puts it on
`request.state`, binds it into the structlog context, and echoes it on every response
including error envelopes. `expose_headers=["X-Request-ID"]` on CORS makes it readable by
the browser.

So `request_id` (§7's first key) is end-to-end today: browser → server log. `correlation_id`
exists on `Event`. `task_id`, `trajectory_id`, `step_id` exist as columns. `tool_call_id` and
`workflow_run_id` exist in their own tables. **No single table joins them**, which is the
§7 gap: the keys exist, the join does not.

---

## 10. Errors as signal — ✅ a real taxonomy already

`src/atlas/infra/errors.py` (141 lines). `AtlasError` carries class-level `code: str`,
`user_message: str | None`, `retryable: bool`. Subclasses: `FatalError`, `RetryableError`,
`UserError`, `SystemError_`, `ConfigError`, `ManifestError`, `RegistryError`, `BusError`,
`ModelError`, `ProviderUnavailableError`, `ToolUnavailableError`, `MemoryError_`,
`StorageError`, `NotFoundError`, `AuthenticationError`, `AuthorizationError`,
`BudgetExceeded`.

**This is the basis for §14 (stable error fingerprints).** `code` is already a stable,
human-chosen string that does not move when a line number moves — far better than hashing a
message. A fingerprint of `(code, module, normalised message, top app frame)` is derivable
without touching any raise site.

`src/atlas/orchestration/monitor.py` (27 lines) adds `ExecutionMonitor.check_may_continue(token)`
and `is_recoverable(exc)` — an existing retryable/fatal split at the execution layer.

---

## 11. Frontend error observability — ⚠️ contained, not reported

Verified in `frontend/lib/api/client.ts` and its test
(`frontend/lib/api/__tests__/client.test.ts`, 285 lines):

- `AtlasApiError` carries `status`, `code`, `detail`, `requestId`; `AtlasTimeoutError` for
  the 8s abort; `AtlasContractError` carries `path` + `issues`.
- `detail` is **truncated to 500 chars** so an error cannot flood the UI.
- `requestId` falls back to the `X-Request-ID` response header when the body has none.
- A network failure stays a `TypeError` — deliberately a different fact from a timeout.
- Boundaries exist: `app/error.tsx`, `app/global-error.tsx`, `app/not-found.tsx`.

So frontend errors are *typed and contained*, and `requestId` means a browser error can be
tied to a server log line by hand. **Nothing reports them back to the backend** — there is
no ingest endpoint, so §40 is a genuine gap. Note the §40 constraint ("without sending
sensitive page state externally") is already easy to honour here: the client's error objects
carry a status, a code, a truncated detail and a request id — not page state.

---

## 12. What is *not* observability, despite looking like it

- `Database.health()` — was `return self._conn is not None`; now executes `SELECT 1`
  (Band A6). Still a liveness bit, not a metric.
- `intelligence/{observability,health,governance,runtime}` have **no `__init__.py`**. They
  are directories that happen to contain modules. Anything importing across them relies on
  implicit namespace packages.
- `docs/final/PERFORMANCE_ARCHITECTURE.md` describes intent; no performance *measurement*
  store exists (§38/§39 gap).

---

## 13. Summary for the layer being built

Reuse, do not rebuild: `structlog` + `bind_context` for correlation; the `events` table for
pipeline health; `llm_calls` for LLM/model regression; `RuntimeSupervisor.HealthReport` for
component transitions; `HealthMonitor` for provider reliability; `AtlasError.code` for
fingerprints; `AtlasApiError` for the frontend contract.

Build new: a durable incident store (§3), a SQL-backed baseline/anomaly layer (§11/§12), the
correlation join table (§7), worker heartbeat/restart tracking (§43), the measured/estimated/
heuristic provenance flag (§9), a frontend error ingest endpoint (§40), and a persistent span
sink **or** an honest re-labelling of `Tracer`.
