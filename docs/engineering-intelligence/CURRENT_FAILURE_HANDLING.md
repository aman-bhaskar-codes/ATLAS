# CURRENT_FAILURE_HANDLING — how ATLAS already reacts to failure

**Method.** Read from the tree this pass. Files cited were opened in full unless a line range
is given.

The headline finding: **ATLAS already has a complete failure-learning control plane, and it
is trajectory-scoped.** It learns from failed *tasks*. It has no notion of a failing
*system*. That distinction is the whole shape of the gap.

---

## 1. The failure taxonomy — ✅ substantial and deterministic

`src/atlas/adaptation/taxonomy.py` (139 lines):

- `FailureClass` — **23 classes**.
- `FailureDomain` — **8 domains**.
- `domain_of(failure_class) -> FailureDomain` — a total mapping.
- `FailureTaxonomy` — the registry/lookup surface.

`src/atlas/adaptation/failure_analyzer.py` (165 lines):

- `FailureAnalyzer` walks `decision_traces` to separate **symptom from root cause**.
- It is **deterministic — no LLM call.** The walk is a backwards scan over recorded decision
  steps to find the first step whose outcome diverged.

**Why this matters for §16 (`RootCauseAnalyzer`).** A deterministic root-cause walk already
exists and works on trajectories. §16's analyser is the *system-scoped* sibling, and §55/§57
demand the same discipline (evidence, contradicting evidence, no certainty without
evidence). `FailureAnalyzer` is the precedent to follow, and where an incident *has* a
trajectory it should be **called**, not duplicated.

**Persisted:** `failure_records`, `failure_taxonomy`, `failure_analyses` tables.

---

## 2. Clustering and dedup — ✅ already solved, for trajectories

`src/atlas/adaptation/clustering.py` (124 lines):

- `ClusterKey(failure_class, component, model, task_class)` — the grouping key.
- `cluster_failures(records) -> list[FailureCluster]`.
- `FailureCluster.is_candidate_evidence` — true only at `>= MIN_EVIDENCE_DEFAULT` (**3**).

**Against §13 and §15.** §13's rule — "If 20 errors come from the same event: DO NOT create
20 independent incidents" — is *already the design* here, with a 4-tuple key and an evidence
floor. §15's `occurrence_count` is the same idea expressed on a single row instead of a
cluster object. The engineering layer should reuse `ClusterKey`'s shape (a small tuple of
stable discriminators) rather than inventing a different grouping scheme, and should treat
the evidence floor as precedent: **a single occurrence is not yet evidence.**

---

## 3. Hypotheses and gating — ✅ and already conservative

`src/atlas/adaptation/hypotheses.py` (240 lines):

- `HypothesisGenerator.from_failure_cluster(...)` and `.from_strategy_underperformance(...)`.
- `HypothesisStore` with `exists_for_component(...)` **dedup** — the same component does not
  accumulate duplicate open hypotheses.
- `AllowedChangeType` — an allow-list of what a hypothesis may propose.
- `HypothesisStatus` — lifecycle.
- `MIN_EVIDENCE_HIGH_RISK = 5` (vs 3 for ordinary changes).
- `HIGH_RISK_CHANGE_TYPES = {MODEL_ROUTING, WORKFLOW_ORDERING, CONTEXT_COMPILATION}`.
- **`SAFETY_BLOCK`, `AUTH_FAILURE`, `USER_CONSTRAINT_FAILURE`, `ENVIRONMENT_FAILURE` map to
  `None` change type** — the code's comment is explicit that ATLAS must *never* generate a
  learning hypothesis for these.

**This is the single most important precedent in the repository for §27–§34 and §83.** The
project has already decided, in code, that:

1. change types are an **allow-list**, not free-form;
2. higher-risk change classes require **more** evidence;
3. some failure classes are **categorically not** ATLAS's to fix — a safety block is not a
   bug, it is the system working.

§83's prohibition list is the same principle applied to system repair. `RepairType` (§28)
should mirror `AllowedChangeType`'s structure, and the "never auto-repair" set should mirror
the `None`-mapped failure classes. Do not invent a second, differently-shaped gate.

Persisted: `hypotheses`, `experiments`, `comparison_results`, `generalization_results`,
`promotion_decisions`, `strategy_versions`, `strategy_performance`, `adaptation_events`,
`negative_experiences`.

---

## 4. Repair-as-experiment — ✅ the machinery §60–§63 asks for already exists

Present under `adaptation/`, with backing tables:

| Concern | Existing mechanism | Table |
|---|---|---|
| Run a change as an experiment | experiment runner | `experiments` |
| A/B a change without exposure | shadow comparison | `shadow_comparisons` |
| Gradual exposure | canary | `canary_deployments`, `canary_observations` |
| Does it generalise beyond the trigger? | generalization | `generalization_results` |
| Promote or reject | promotion decision | `promotion_decisions` |
| Regression suite result | regression runner | `regression_results` |
| "What if we had chosen X?" | counterfactual engine | `counterfactuals` |
| Was the decision good, separate from the outcome? | decision quality | `decision_quality` |
| Is confidence calibrated? | calibration | `calibration_records` |

**Against §60 ("repair as experiment"), §62 (regression gate), §63 (canary) and §26
(counterfactual debugging).** These are not new capabilities. §26 says outright to reuse the
existing Counterfactual Engine. §60–§63 should route a `RepairHypothesis` through the
*existing* experiment → regression → canary → promotion path, adding only what is genuinely
missing: the security gate (§33) and the repair-specific audit (§86).

---

## 5. Task-level failure settling — ✅ fixed in the robustness pass

`src/atlas/interfaces/api/facade.py`: the background task spawned by `create_task` is held in
a set (via `infra/tasks.py::spawn`), and on exception the `tasks` row is marked `failed` with
the error recorded in `payload`. Before that fix a raising `orchestrator.run` left the row at
`'created'` forever and `active_task_count` never dropped.

`src/atlas/infra/tasks.py`: module-level `_TASKS: set`, `_on_done` (discard + log
`task.exception()`), `spawn(coro, *, name)`, `pending_count()`.

**Relevant to §44 (task stall detection).** A task can now fail *loudly*. What is still
undetected is a task that neither completes nor raises — one that simply stops making
progress. `tasks` rows carry timestamps and `task_events` records progress, so stall
detection is a query over "non-terminal row whose newest `task_events` row is older than
N", with no new instrumentation required.

---

## 6. Dead letters and retries — ✅ durable

- `dead_letters` table.
- The `events` table's `attempt_count`, `next_retry_at`, `dead_letter_reason`.
- `MessageBus` dead-letters a payload it cannot deserialize rather than dropping it.
- `infra/circuit_breaker.py` + `HealthMonitor`'s per-provider breaker.

Nothing *reads* dead letters as an alerting signal. §42 needs a reader, not a writer.

---

## 7. HTTP-layer failure handling — ✅ correct and load-bearing

`src/atlas/interfaces/api/app.py` + `errors.py`:

- Exception handlers are registered per **concrete** type (`DeniedError`, `HaltedError`,
  `BudgetExceededError`, …) — *not* for `Exception`. Reason, recorded in the plan and worth
  restating because it constrains any new router: Starlette's middleware stack is
  `[ServerErrorMiddleware, *user_middleware, ExceptionMiddleware]`, `add_middleware` inserts
  at index 0 (so the **last** added is outermost), and a handler for `Exception` lands on
  `ServerErrorMiddleware`, which is outside CORS and can therefore never carry CORS headers.
  A 500 without CORS headers reaches the browser as `TypeError: Failed to fetch` with no
  status.
- The single `_request_middleware` catches around `call_next` and returns the 500 envelope
  itself.
- CORS is added **last**; `allow_methods=["GET","POST","PUT","DELETE"]`,
  `expose_headers=["X-Request-ID"]`.
- Every error path logs — `warning` for mapped 4xx, `exception` for the 500 — and `detail`
  never carries a traceback.
- `_SAFE_METHODS = frozenset({"GET","HEAD","OPTIONS"})` with `_reject_readonly_mutation` for
  `ro:` principals.
- 16 `include_router` calls; `auth_required = [Depends(require_principal)]` on all but
  `health_router`, `memory_router`, `events_ws_router`.

**Implication for new engineering routes:** register them with `auth_required`, and if any of
them raises a new domain error, add a **concrete-type** handler. Never register `Exception`.

**Known gap carried from `docs/final/TECHNICAL_DEBT_FINAL.md` #20:** WebSocket routes carry no
auth, because `require_principal` takes a `Request` which does not resolve for a WebSocket
scope. Any incident-streaming WebSocket inherits this gap and must not be assumed
authenticated.

---

## 8. What is missing

1. **No system-scoped failure record.** Everything above keys off a trajectory or a task. An
   exception in a background worker, a health-check transition, a dead-letter spike, a schema
   mismatch, a provider outage — none of these produce a durable record that anyone can list,
   triage, or close. There is **no `incidents` table** among the 88 that exist.
2. **No severity concept.** `FailureClass` classifies *kind*, never *urgency*. §4 requires
   severity to control autonomy; there is nothing to read.
3. **No lifecycle.** `HypothesisStatus` tracks a hypothesis, not an incident. §3's twelve
   statuses (DETECTED → CLOSED) have no equivalent.
4. **No occurrence counting on a single record.** Clustering computes groups on read;
   nothing increments an `occurrence_count` on a stored row (§15).
5. **No repair path that touches the system rather than a strategy.** The adaptation plane's
   change types are all *behavioural* (routing, ordering, context). §28's `CONFIG`, `CODE`,
   `DEPENDENCY`, `UI_CONTRACT` repairs have no precedent and no isolation mechanism — see
   `CURRENT_SELF_REPAIR.md`.
6. **No escalation.** Nothing ever says "this needs a human". §84's "if repair fails twice:
   STOP and escalate" has no channel to escalate *to*.
