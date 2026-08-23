# GAPS — what is genuinely missing, and what must not be rebuilt

**Method.** This document is the synthesis of the six `CURRENT_*.md` audits in this
directory. Every claim traces to a file read during the audit pass. Where a fact could not be
verified, it is marked **UNVERIFIED** rather than asserted.

Spec §2 requires this audit before any code changes, and §1 requires that the result be used
to *avoid* duplication. The single most useful output of the audit is therefore section 3:
**what not to build.**

---

## 1. The gap, in one sentence

ATLAS has a mature **trajectory-scoped** failure-learning plane and no **system-scoped**
incident plane: it learns from tasks that went wrong, and has no durable record, severity,
lifecycle, or repair path for the system itself going wrong.

Everything below is a consequence of that one sentence.

---

## 2. Hard findings

These are the load-bearing facts. Each was verified directly.

1. **There is no `incidents` table.** 88 `CREATE TABLE` statements exist in
   `src/atlas/infra/db.py`; none of them is an incident, security incident, or repair record.
   The §3 `Incident` model is genuinely new — not a rename of something existing.

2. **`Metrics` has zero call sites.** `src/atlas/infra/metrics.py` defines
   `counter/gauge/observe/snapshot`; the object is constructed in
   `bootstrap/infrastructure.py` and threaded into `app.py` and `bootstrap/runtime.py`, and
   grep over `src/` for `metrics.counter|metrics.gauge|metrics.observe` returns nothing.
   `snapshot()` always returns empty.

3. **`Tracer` has zero call sites.** `src/atlas/infra/tracing.py` defines an async
   `span()` context manager that emits one `debug` log. Nothing is ever timed, there is no
   span store, no span id, and no parent/child linkage. **§74's trace explorer has no data
   source in `Tracer`** — it must be built on `trajectories` / `decision_traces` / `llm_calls`.

4. **`RuntimeSupervisor._worker_registry` tracks two workers and no health.** Entries:
   `embedding_worker`, `scheduler`. No heartbeat, no last-success timestamp, no restart
   count, no queue lag. §43 asks for all five.

5. **The adaptation plane refuses to learn from four failure classes by design.**
   `SAFETY_BLOCK`, `AUTH_FAILURE`, `USER_CONSTRAINT_FAILURE`, `ENVIRONMENT_FAILURE` map to
   change type `None` in `adaptation/hypotheses.py`. This is the existing, in-code precedent
   for §83's prohibition list — the principle is already established, not being introduced.

6. **A regression gate already exists.** `RunReport.gate_passed` in
   `evaluation/service.py` is `failed == 0 and not regressions`, where `regressions` is
   populated from a baseline read *before* the current run's insert. §62 should call it.

7. **ATLAS does not run Ragas.** `evaluation/rag_metrics.py` implements Ragas-**compatible
   metric names** with ATLAS-native deterministic token-overlap implementations, and its
   docstring says so explicitly. §49's UI and docs must say "Ragas-style", never "Ragas".

8. **No git library is installed.** §30's worktree isolation must drive the `git` binary via
   `asyncio.create_subprocess_exec` (ruff `ASYNC220` is enabled tree-wide).

9. **WebSocket routes carry no authentication.** `require_principal` takes a `Request`, which
   does not resolve in a WebSocket scope, so `memory_router` and `events_ws_router` are
   included without it. **Therefore incident data must not be streamed over a WebSocket** —
   it would be an unauthenticated read of security-relevant data. Use SSE behind
   `auth_required`, or poll.

10. **`src/atlas_cli` is neither type-checked nor coverage-measured**, yet it is the shipped
    `atlas` entry point. New CLI commands must be thin wrappers over `atlas.engineering`.

11. **UNVERIFIED — exact `_MIGRATIONS` element count.** The tuple spans lines 20–1315 of
    `infra/db.py`; the count could not be computed because the Bash tool was intermittently
    unavailable. This does not block the work: `_apply_migrations` slices
    `_MIGRATIONS[current:]`, so appending is correct regardless. The next comment label is
    `# 022`.

12. **UNVERIFIED — `interfaces/api/errors.py:108-111` under mypy strict.** It calls
    `app.add_exception_handler(exc_type, functools.partial(_handle_mapped, status=…,
    code=…))` with no `# type: ignore`. Whether mypy accepts the partial against Starlette's
    `ExceptionHandler` alias is unconfirmed because no gate has been run in this or the
    previous session.

13. **No gate has been executed in this session or the previous one.** `pytest`, `ruff`,
    `mypy`, `lint-imports` and every frontend gate are unrun. This is the largest standing
    risk in the whole effort and is not a documentation problem.

---

## 3. What must NOT be rebuilt

§1 is explicit: no parallel debugging system, no second observability system, no second
evaluation system, no second safety/security system, and no `BugAgent` / `DebugAgent` /
`ObservabilityAgent` as independent brains. Concretely, that means the following are **calls,
not implementations**:

| Temptation | Call this instead |
|---|---|
| A new logger with correlation ids | `infra/logging.py` — `bind_context(**kv)`; `merge_contextvars` is already first in the chain |
| A new event pipeline | `infra/bus.py` — `MessageBus`, and `subscribe_global` to ingest |
| A new "did it break?" comparison | `EvaluationService.run_suite` → `RunReport.gate_passed` |
| A new failure classifier | `adaptation/taxonomy.py` — 23 `FailureClass`, 8 `FailureDomain`, `domain_of()` |
| A new symptom-vs-cause walk | `adaptation/failure_analyzer.py` (deterministic, no LLM) |
| A new grouping/dedup scheme | `adaptation/clustering.py` — `ClusterKey`, evidence floor of 3 |
| A new statistics helper | **check `adaptation/statistics.py` first** — §11 explicitly wants only rolling baseline / moving average / percentiles / z-score / robust deviation |
| A new experiment/canary/promotion runner | `adaptation/{experiments,shadow,canary,generalization,promotion}.py` |
| A new counterfactual engine | `adaptation/counterfactual.py` (§26 says to reuse it) |
| A new redactor | `safety/engine.py::_redact_payload` + `_SECRET_FIELDS` + `_SECRET_PATTERN` (do not copy the pattern list — export it) |
| A new tool authorisation path | `SafetyEngine.guard()` — "nothing executes a tool except through `guard()`" |
| A new tamper-evident log | `audit_events` with `prev_hash`/`row_hash`, verified by `doctor`'s `audit.chain` |
| A new memory store | the existing memory architecture (§90: "Do not create isolated memory infrastructure") |
| A new health model | `RuntimeSupervisor` — `SystemState`, `ComponentStatus`, `ComponentHealth`, `HealthReport` |
| A new provider reliability tracker | `intelligence/health/health_monitor.py` |
| A new LLM call recorder | `infra/llm_tracker.py` → `llm_calls` |
| A new scheduled-job mechanism (§66) | `CronScheduler.register_job(name=…, cron=…, fn=…)` |
| A new background-task spawner | `infra/tasks.py::spawn` (`RUF006` is enforced) |
| A new error taxonomy | `infra/errors.py` — `AtlasError.code` is already a stable fingerprint input |
| A new frontend error type | `AtlasApiError` / `AtlasTimeoutError` / `AtlasContractError` in `frontend/lib/api/client.ts` |

---

## 4. What must be built

Grouped by the concern they serve, with the spec sections they satisfy.

### 4.1 Domain and storage (foundation — everything depends on it)

- `Incident` model, ~25 fields, 12 statuses `DETECTED → CLOSED` (§3).
- `Severity` — `INFO / LOW / MEDIUM / HIGH / CRITICAL`, where severity **controls autonomy**
  (§4). Nothing today has a severity concept; `FailureClass` classifies kind, never urgency.
- `SecurityIncident` as a **separate** model (§52).
- Error fingerprinting from `(AtlasError.code, module, normalised message, top app frame)` —
  stable across line-number churn (§14).
- Dedup via `occurrence_count` on a stored row (§15) — distinct from clustering-on-read.
- Correlation join across `request_id / correlation_id / task_id / trajectory_id / step_id /
  tool_call_id / workflow_run_id` (§7). All seven keys exist; **no table joins them.**
- Migration `# 022` appended to `_MIGRATIONS`, `CREATE TABLE IF NOT EXISTS` throughout.

### 4.2 Detection

- Baselines per task class / capability / provider / model / tool / workflow / strategy (§12),
  computed **from SQL**, not from in-process `Metrics` — a rolling baseline that resets on
  restart is not a baseline.
- Simple statistical anomaly detection only (§11).
- Detectors: event pipeline health (§42 — every field is already an `events` column), worker
  heartbeat/crash-loop (§43), task stall (§44), deadlock/loop (§45), resource leak (§46), DB
  integrity (§47), memory integrity (§48), RAG regression (§49), computer-use (§50), safety
  events surfaced even on success (§51), API contract mismatch (§41), frontend error ingest
  (§40), model regression over `llm_calls` (§10).
- The measured / estimated / heuristic provenance flag (§9) — currently absent from every
  data model.

### 4.3 Diagnosis

- `RootCauseAnalyzer` → `RootCauseCandidate[]` with cause, confidence, evidence,
  affected components, supporting events, **contradicting evidence** (§16, §57).
- Causal graph (§17); recent-change correlation that does **not** declare causation (§18);
  regression localisation (§19); SelfIndex retrieval that never ingests the whole repo (§20,
  §21, §55); runtime state correlation (§22); structured bug reports (§23).
- Reproduction modes (§24) with dangerous external actions excluded; trajectory replay (§25).
- Ranking that does not use LLM confidence alone (§59); concrete-evidence requirement (§58).

### 4.4 Repair

- `RepairHypothesis` (§27) and 9 repair types (§28) shaped after `AllowedChangeType`.
- Pipeline (§29) with **no** path from incident to production source edit.
- Worktree isolation keyed to `incident_id` (§30), via `git` subprocess.
- Patch boundary deny-list evaluated on concrete paths **before** any write (§32) —
  `src/atlas/safety/**`, `credentials/**`, deployment secrets.
- Security gate returning `HUMAN_REVIEW_REQUIRED` (§33); eligibility limited to low-risk
  classes (§34).
- Fail-before / pass-after regression test generation (§35) and a test-quality check that
  rejects a test which passes trivially (§98). Without the fail-before half, §97's
  self-expanding suite is unsafe.
- Validation pipeline where tests are not sole proof (§36, §37).
- Repair limits — stop and escalate after two failures (§84); loop guard via
  `repair_chain_id` / `depth` / `parent_incident` (§85); full audit into the existing hash
  chain (§86); patch rollback preserving evidence (§64).
- Autonomy levels, **defaulting to LEVEL 1/LEVEL 2** (§81); auto-remediation only when
  explicitly configured (§82); §83's prohibition list enforced in code, not in a prompt.

### 4.5 Surfaces

- CLI (§112): `atlas incidents [active|critical]`,
  `atlas incident show|diagnose|repair|verify|approve|rollback <id>`, `atlas self-check`,
  `atlas system-health`, `atlas explain-issue`, `atlas major-issues`; plus `atlas verify` and
  `atlas learn doctor` named by §125. All absent. All belong in `src/atlas_cli/main.py`.
- API routers registered in `create_app()` **with `auth_required`** and with
  **concrete-type** exception handlers only — never a handler for `Exception`, which lands on
  `ServerErrorMiddleware` outside CORS and reaches the browser as `Failed to fetch`.
- Frontend: `/system/incidents` (§67), incident detail (§68), self-repair console (§69), diff
  view (§70), home "System Attention" (§71), `/system/observability` (§72), LLM observability
  (§73), trace explorer (§74), `/system/security` (§75), evaluation centre (§76),
  `/engineering` (§114), final dashboard (§113). **No `/system/*` or `/engineering` route
  exists today.**
- Reports: Major Issue Summary (§65, §119), daily engineering report from real telemetry
  (§66), `SystemScorecard` with dimensions kept **separate** (§105).

### 4.6 Learning and knowledge

- `KnownFailurePattern` library (§88); root-cause knowledge that still requires verification
  (§89); `EngineeringMemory` built on the **existing** memory architecture (§90); incident
  knowledge graph (§99); learning from fixes feeding Experience → Strategy → Evaluation (§87).

### 4.7 Change intelligence

- Change impact analysis (§100), pre-deployment risk (§101), post-change monitoring (§102),
  change → incident correlation (§103), `ReleaseHealth` (§104).

### 4.8 Tests and docs

- Six E2E tests (§106–§111): worker failure, sandbox software bug, RAG regression,
  performance regression, prompt injection, frontend/backend contract mismatch.
- The §124 doc set: `ARCHITECTURE`, `OBSERVABILITY`, `INCIDENTS`, `ROOT_CAUSE`,
  `SELF_REPAIR`, `SECURITY`, `EVALUATION`, `OPERATIONS`, `PLAYBOOKS`.

---

## 5. Placement decision

Insert `atlas.engineering` into `importlinter.ini`'s layer list **between `atlas.diagnostics`
and `atlas.adaptation`**:

```
atlas.interfaces
atlas.diagnostics
atlas.engineering      ← new
atlas.adaptation
atlas.evaluation
atlas.orchestration
atlas.knowledge
atlas.capabilities
atlas.memory
atlas.intelligence
atlas.safety
atlas.tools
atlas.control
atlas.perception
atlas.infra
```

Why this position and not another:

- It can import **adaptation, evaluation, orchestration, knowledge, capabilities, memory,
  intelligence, safety, tools, control, perception, infra** — i.e. every subsystem it must
  read from and every gate it must call.
- It can be imported by **interfaces** (API routes, CLI) and **diagnostics** (`self-check`).
- **`atlas.safety` cannot import it**, which is the property that matters most: the authoriser
  must not depend on the thing it authorises.
- It matches §123's diagram, which places the engineering layer above adaptation.
- It satisfies §1's "not a fifth independent brain" — a layer that sits *above* adaptation and
  *calls down* is a coordinator, not a peer.

Note the `infra-knows-no-policy` contract means the incident **store** cannot live in
`atlas.infra` if it consults safety for redaction — and it must. The store belongs in
`atlas.engineering`.

---

## 6. Build order

Foundation first, because §4's "severity must control autonomy" and §29's pipeline are
meaningless without a durable incident.

1. **Domain + storage + migration `022`** — `Incident`, `Severity`, statuses,
   `SecurityIncident`, fingerprint, `occurrence_count`, correlation keys, store, importlinter
   entry. Tests land with it (coverage `source = ["atlas"]` means untested new code lowers
   the measured total against `fail_under = 63`).
2. **Ingest + a small number of real detectors** — `subscribe_global` on the bus, the
   `RuntimeSupervisor` health-transition hook, event-pipeline health, worker heartbeat, task
   stall. These are queries over existing columns, so they produce real incidents immediately.
3. **Baselines + anomaly detection** from SQL, reusing `adaptation/statistics.py`.
4. **Diagnosis** — `RootCauseAnalyzer`, evidence, confidence with contradicting evidence,
   ranking. Calls `FailureAnalyzer` where a trajectory exists.
5. **Repair** — hypothesis, eligibility, patch-boundary deny-list, security gate, worktree,
   fail-before test capture, validation via `EvaluationService`, audit, rollback, limits and
   loop guard. Autonomy defaults to LEVEL 1/LEVEL 2.
6. **Surfaces** — CLI, then API, then frontend. Frontend last, because a UI over an empty
   store is the exact failure mode the truth mandate forbids: it renders "0 incidents" as if
   that were a measurement.
7. **Reports, learning, change intelligence.**
8. **E2E tests + the §124 doc set.**
9. **Gates**, in the §125 order.

---

## 7. Honest limitations to carry into the final report (§120)

- Telemetry the spec assumes (`Metrics`, `Tracer`) is presently inert. Until it has call
  sites, some §6/§72/§74 surfaces will be thinner than the spec's description, and the UI must
  say so rather than render an empty chart as a healthy one.
- `/system/security` cannot stream over WebSocket while finding 9 stands.
- No autonomous repair of destructive operations, credential handling, safety policy, or
  data mutation. Per §120 this is **correct behaviour**, not a shortfall.
- A daily report over an empty incident store must distinguish *no incidents detected* from
  *no detectors running*.
- 94 files fail `ruff format --check`, so `just check` will stay red for pre-existing reasons;
  judge this work by `ruff check .`, `mypy`, `lint-imports` and `pytest` individually.
- Outstanding items from `docs/final/TECHNICAL_DEBT_FINAL.md` that this layer touches but does
  not fix: #13 (four unvalidated frontend API surfaces), #17/#18 (approval undercount,
  `decide_approval` unimplemented), #19 (non-crash-consistent WAL backup), #20 (WebSocket
  auth), #23/#24 (dead capability controls), #26 (format gate), #29 (narrow mypy/coverage
  scope).
