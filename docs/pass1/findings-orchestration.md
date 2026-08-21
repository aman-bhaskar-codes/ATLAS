# Phase 0 Findings — Orchestration Internals

34 modules + `managers/`. No `context.py`, no `budget.py`, no `verify.py`, no `plan_version`.
**Two** independent checkpoint modules (one dead).

## Contracts

All orchestration contracts are frozen pydantic **except `GoalState` and `VerificationResult`,
which are MUTABLE dataclasses.**

`orchestration/types.py` (all `frozen=True`):
- `RiskLevel` :20 — LOW|MEDIUM|HIGH
- `Capabilities` :26 — `needs_memory=True, needs_retrieval=True, needs_tools=False,
  needs_reasoning=True, needs_confirmation=False, needs_cloud=False, max_risk=LOW`
- `PlanStep` :39 — `index, intent, tool, operation, args, depends_on, expected_output`
- `Plan` :50 — `goal, constraints, steps, termination_conditions, risk, estimated_cost_usd,
  confidence, unknowns`. **No `version`, no `id`, no `parent_plan_id`.**
- `Thought` :62, `ActionKind` :70 = `Literal["tool_call","final_answer","ask_user","noop"]`,
  `Action` :73 — `step, kind, tool, operation, args, final_text`
- `Observation` :83 — `step, ok, content: Any, error`
- `TaskResult` :91 — 14 fields; `actions`/`observations` typed `tuple[Any, ...]` (typed
  boundary lost)
- `Task` :110, `Critique` :128

`orchestration/goal.py`:
- `GoalState` :34 **mutable dataclass** — `objective, constraints, success_criteria,
  current_state, progress, confidence, replan_count, max_replans=3, created_ts`
- `VerificationResult` :86 **mutable dataclass** — `passed, score, criteria_results,
  failure_reason, suggestions`
- `Verifier(Protocol)` :112 — `verify(goal, answer, context="")`

`memory/trajectory.py`: `ActionRecord` :124, `ObservationRecord` :137, `Trajectory` :148
(27 fields), `DecisionPoint` :26 (8 members), `DecisionTrace` :48, `FailureCategory` :77
(9 members), `FailureRecord` :91.

## P0 — VERIFICATION IS DEAD IN PRODUCTION

`GoalVerifier` is the default (`bootstrap/orchestration.py:158`, `CritiqueCfg.enabled=True`).
But `goal.py:177-178`:
```python
if not goal.success_criteria:
    return VerificationResult(passed=True, score=1.0)
```
`GoalState` is constructed in exactly two places — `orchestrator.py:159-164` and
`reasoning.py:131` — and **neither sets `success_criteria`**. Nothing in `src/` ever assigns
it (only 5 test files do). The planner prompt has no field that could populate it.

Consequences:
1. `GoalVerifier` always returns `passed=True, score=1.0` **without an LLM call**
2. The verification-failure replan branch `reasoning.py:197-234` is **unreachable**
3. `TaskResult.verification_passed` is always `True`, `verification_score` always `1.0`
4. `Trajectory.verification_*` carries meaningless constants → experience extraction learns
   from a constant

**It also fails OPEN**: `goal.py:226-234` catches bare `Exception` and returns
`VerificationResult(passed=True, score=0.5, failure_reason="verifier_error")`. A crashed
verifier is indistinguishable from a pass; no caller inspects `failure_reason`.

**Not capability-aware.** `goal.py:10-12` claims "a coding task verifies via tests; a research
task verifies via source cross-check" — there is no test-runner verifier, no cross-check
verifier, no registry, no dispatch. One instance chosen once at bootstrap for all tasks;
`caps` is never passed to `verify()`. **Phase 12 is greenfield.**

## P0 — No TaskIntent exists (Phases 1-2 greenfield)

`router.py` (89 lines) outputs `Capabilities` — a 7-flag bag, not an intent. Prompt verbatim
(`router.py:22-26`):
```
Classify a user request for an agent runtime. Output ONLY JSON:
{"needs_tools":bool,"needs_reasoning":bool,"needs_cloud":bool,
 "needs_confirmation":bool,"max_risk":"low|medium|high"}
```
Pre-LLM signal is one keyword scan :36 — `("file","open","run","delete","send","install","search")`.
Fails cautious on exception :65 → `needs_tools=tool_hint, needs_confirmation=True, max_risk=MEDIUM`.

Phase 2 field audit:

| field | exists? | where |
|---|---|---|
| objective | yes | `GoalState.objective` (from `plan.goal`) |
| domain | **NO** | zero hits |
| constraints | yes | `Plan.constraints`, `GoalState.constraints` |
| success_criteria | declared, **never populated in prod** | `goal.py:46` |
| risk | yes | `Plan.risk`, `Capabilities.max_risk`, `Task.max_risk` |
| privacy_level | type exists, never task-derived | `PrivacyClass`; orchestration never sets it |
| urgency | **NO** | zero hits in src |
| complexity | not in orchestration | only in the unwired `agents/` stack |
| required_capabilities | MODEL caps only, hardcoded per call site | `ModelRequest.required_capabilities` |
| likely_side_effects | **NO** | — |
| verification_requirements | **NO** | — |

**~70% of the router's output is dead.** Only `caps.needs_reasoning` and
`caps.needs_confirmation` are consumed (`planner.py:64-65`). `needs_tools`, `needs_memory`,
`needs_retrieval`, `needs_cloud`, `caps.max_risk` have zero consumers. `Replanner.replan`
accepts `caps` (`replanner.py:94`) and **never reads it**, despite `reasoning.py:123`
documenting "capability-aware plan revision".

## Context compilation — two modules, one wired

**`context_builder.py` (WIRED).** `token_budget: int = 3000` :32, never overridden by
bootstrap. Estimator `len(text)//4` :41. Layers by priority :56-65 — `system`(0),
`safety`(1), `user_model`(2), `tools`(3) ← `registry.catalog()`, `memory`(4), `working`(5),
optional `plan`(3).

The budget logic :71-79 — **priorities 0-2 can overflow without limit**; only 3+ are
droppable, and dropping is all-or-nothing per layer (no truncation):
```python
if used + cost > self._budget and ly.priority > 2:
    continue
```

**Context is built exactly ONCE per task**, before planning (`orchestrator.py:128-134`). The
same immutable string is passed to every reasoning step. Never rebuilt, never re-retrieved
mid-loop. Appended to once with DAG observations (:173).

**`context_engine.py` (NOT WIRED).** `ContextBudget(total=6000, max_history_turns=8)`,
`history` property → 4000. `ContextRanker.score()` :43 (recency + `failure_boost=0.35`).
`ContextCompactor.compact()` :57 keeps newest turns within budget and prepends a synthetic
`[compact] N earlier steps summarized` thought.

`grep "compact("` → 3 hits: the def, `reasoning.py:104` (construction), `reasoning.py:389`
(`render(history[-4:])[:1500]`, checkpoint summary only). **`compact()` is never called.
`ContextRanker` is unused in `src` entirely.** `_summary_thought()` :117 is an identity
function.

So `prompt_builder.py:30-36` walks the **full unbounded history**, one `thought:` + one
`observation: {status} {str(content)[:300]}` per turn. The only production trimming is
`history = history[-3:]` after a replan (`reasoning.py:224`, :352). No LLM summarization of
history anywhere. **Phase 24 must wire this.**

## Planning

`planner.py:23-31` prompt demands the full `Plan` JSON schema; user prompt :57 is
`f"CONTEXT:\n{context}{knowledge_block}\n\nREQUEST:\n{request}"`. `prior_knowledge`
(`orchestrator.py:218-246`) = 5 skills + 5 experiences + 8 world facts, truncated to 4000
chars — **all three sources are structurally empty** (see findings-memory.md).

Model request :58-67 — `{PLANNING, JSON_GENERATION}`,
`needs_deep_reasoning=caps.needs_reasoning`, `stakes_tier=CONFIRM if needs_confirmation`,
`max_tokens=2048`, temperature defaults to 0.2.

**Confidence is LLM self-reported**, `plan_parsing.py:58` —
`float(str(raw_conf)) if raw_conf is not None else 0.5`. No calibration, no [0,1] clamp, no
try/except (a non-numeric value raises `ValueError`). Consumed once:
`reasoning.py:471` `needs_deep_reasoning=(plan.confidence < 0.6)`.

Replanning uses a **distinct** `_REPLAN_SYSTEM` (`replanner.py:30-39`) with the same schema
plus "Address the specific failure. Do not repeat what already failed."

## Replanning

- Budget: `GoalState.max_replans = 3` (`goal.py:53`). The comment says "configurable via
  ExecutionLimits" — **`ExecutionLimits` has no `max_replans` field.** Never overridden.
- Gate: `should_replan()` (`replanner.py:56-86`, rule-based, no LLM) → `goal.can_replan()`
  and (`not last_obs.ok` or `verification.score < 0.5`)
- `record_replan()` called at `reasoning.py:215` (verification path — **unreachable**) and
  `:344` (tool-failure path — the only live one)
- Events: `replan.started` with `trigger="verification_failed"|"tool_failure"`, then
  `replan.finished` with `replan_count` + `new_goal`
- **Parse failure returns the ORIGINAL plan** (`replanner.py:142-151`) while still having
  consumed a replan slot → guarantees the same failure recurs until the limit
- **No plan version recorded anywhere.** `Plan` has no version/id/parent; the loop just
  rebinds `current_plan` (:216, :345). The trajectory stores the **original** plan
  (`orchestrator.py:280-283`), so plan lineage is unreconstructable. **Phase 11 needs this.**

## Recovery / retry (`managers/retry.py`, 35 lines)

Backoff: exponential base 0.5s → cap 8.0s, +0–25% jitter.

**The entire exclusion rule is `if not exc.recoverable`**, inside `except OrchestrationError`
— so any non-`OrchestrationError` propagates unretried. Retried: `PlanningError`,
`ReasoningError`, `ToolExecutionError`, `ValidationError`, `VerificationError`,
`OrchestrationTimeoutError`. Not retried: `ContextError`, `OrchestrationMemoryError`,
`CancellationError`, `RecoveryError`, `IllegalTransitionError`.

**Safety blocks and permission denials are excluded only BY ACCIDENT OF SHAPE, not by rule.**
`dispatcher.py:51-59` converts `HaltedError` → `Observation(ok=False, error="halted: ...")`
and `DeniedError` → `Observation(ok=False, error="denied (tier N): ...")` — they never raise,
so `RetryManager` never sees them. There is **no `SafetyBlockedError`, no permission-error
class, no explicit non-retryable list.** Phase 13 requires one.

**Invalid input IS retried** — `ValidationError.recoverable = True`, and worse
`dispatcher.py:60-61` wraps *every* other exception from `safety.guard()` (including a tool's
own `ValueError`/`TypeError` on bad args) into `ToolExecutionError(recoverable=True)`. A
deterministically-invalid call is retried 3× with backoff. No idempotency check before retry:
`ToolMetadata.idempotent` is consulted only by `resume.py:30-43`.

**The retry budget is per-TASK, not per-call.** `counter.tick_retry()` increments a shared
`LimitCounter.retries`, never reset between dispatches → `max_retries=3` is **3 total for the
entire task** across all tool calls. Exhaustion is silent (returns `False`).

Crash recovery (`recovery.py`) is FAIL-CLEAN: marks every non-terminal `tasks` row failed and
prunes checkpoints. Its `_NON_TERMINAL` :18-30 **duplicates 11 state strings as literals**
instead of deriving from `TaskState`.

`managers/timeout.py` is used **once**, on the reasoning model call (120.0s,
`reasoning.py:80`). **Tool dispatch has no timeout.**

## Task state machine (`state.py`, 136 lines)

15 states: CREATED, READY, BUILDING_CONTEXT, PLANNING, REASONING, WAITING_TOOL,
WAITING_CONFIRMATION, EXECUTING, OBSERVING, VALIDATING, RETRYING, CANCELLING, FAILED,
COMPLETED, ARCHIVED. `_TERMINAL = {FAILED, COMPLETED, ARCHIVED}`. Legal-transition table
`_LEGAL` :37-108; every non-terminal entry also permits CANCELLING.

**Ownership is split.** The instance is created per-run by `Orchestrator.run`
(`orchestrator.py:104`), which drives `READY → BUILDING_CONTEXT → PLANNING`, then hands the
**same mutable object** to `ReasoningLoop.run(machine=...)` (:187), which drives all remaining
transitions. **Only the Orchestrator persists it — once, in `finally`** (:212-216). So the DB
sees only the *final* state; intermediates exist only as bus events and in-memory
`_history` (`machine.history` and `is_terminal()` have zero callers in `src`).

**Three dead states**: `WAITING_CONFIRMATION`, `RETRYING`, `ARCHIVED` are never transitioned
to. Confirmation happens inside `SafetyEngine.guard()` and retries inside `RetryManager`,
both without a state transition. So `recovery.py` lists rows that can never exist.

## `dag_executor.py` (92 lines) — runs IN ADDITION to the OTAR loop

Selected at `orchestrator.py:170` when `plan.steps and all(s.tool and s.operation ...)`.
**The reasoning loop always runs afterward** (:182); DAG output is merely appended to the
context string (:173-176) and progress set (:177-180). **Nothing prevents the reasoning loop
from re-invoking the same non-idempotent tools the DAG already ran.**

Batching :54-79 — `asyncio.gather` per wave, `_MAX_CONCURRENCY = 3` semaphore. Dependencies
not present in `executable` are **silently ignored** by the `if d in executable` filter, so a
step can run though a prerequisite never executed. Empty batch → logs `dag.no_progress` and
breaks (the only cycle handling; no topological validation).

**SafetyEngine: yes, unconditionally** — `_run_step` :82-91 goes through the same
`ToolDispatcher.dispatch` → `SafetyEngine.guard`. No tier bypass.

But it bypasses **everything else**: no `SelfCritique.critique()` pre-action gate, no
`LimitCounter.tick_tool()` (DAG tool calls don't count against `max_tool_calls`), no
`RetryManager`, no `ExecutionRecorder`, **no `ExecutionMonitor.check_may_continue()` (kill
switch and cancellation are not checked per DAG step)**, no event emission, no trajectory
records, no state transitions.

## Every bounded-effort knob

| bound | default | production value | enforcement |
|---|---|---|---|
| `max_steps` | 12 | **15** (`bootstrap:154`) | `tick_step()` → `ReasoningError` |
| `max_tool_calls` | 20 | 20 | `tick_tool()`; **not called by DAG** |
| `max_tokens` | 40_000 | 40_000 | `add_tokens()` |
| `max_runtime_s` | 300.0 | 300.0 | **only checked inside `tick_step()`** — a long tool/model call cannot be interrupted mid-flight |
| `max_recursion` | 3 | — | **never read; `LimitCounter.recursion` never incremented** |
| `max_retries` | 3 | 3 | per-task cumulative |
| `max_replans` | 3 | 3 | on `GoalState`, never configurable |
| model timeout | 120.0s | 120.0s | `with_timeout`, reasoning call only |
| retry backoff | 0.5s → 8.0s | same | — |
| DAG concurrency | 3 | 3 | semaphore |
| `ContextBuilder` budget | 3000 | 3000 | partial (pri > 2 only) |
| `ContextBudget` 6000/8/4000 | — | **unwired** | — |
| retrieval budget | 1500 | 1500 | `memory/retrieval.py:39` |
| `CritiqueCfg.min_tier`/`revise_max` | 2 / 1 | — | **zero consumers**; hardcoded at `tiering.py:34` and `self_critique.py:101` |

**There is no notion of reasoning depth / effort tier / thinking budget driven by task
properties.** The only two adaptive knobs are `needs_deep_reasoning=(plan.confidence < 0.6)`
and `stakes_tier=CONFIRM if plan.risk != low`. **Phase 10's L0-L4 is greenfield.**

## Unwired / dead / contradictory (24 items, condensed)

1. `ContextCompactor.compact()` never called; `ContextRanker` unused in `src`
2. Verification dead in production (above)
3. Verifier fails open on exception
4. Router output ~70% dead; `Replanner` ignores `caps`
5. **`ToolRouter` is not in the reasoning path** — built at `bootstrap:152`, exposed only to
   `routes_ops.py:70-71` (which reaches into private `_registry`/`_health`). Prompts use plain
   `registry.catalog()`. `ToolRouter.rank()` does `del intent` (`tool_routing.py:76`) —
   "reserved for … once the planner emits structured intents." **Phase 8 needs this.**
6. **`ToolMetadata` registered for only 2 tools** (`filesystem`, `shell`, `bootstrap:87-104`);
   every other tool gets `metadata=None` → omitted from `all_metadata()`/`tool_call_specs()`/
   `ToolRouter.rank()`, and `resume` refuses to resume them
7. Three dead task states; `machine.history`/`is_terminal()` uncalled
8. Only the terminal task state is persisted; `recovery.py` duplicates state strings
9. **Two unrelated `CheckpointStore` types** — `orchestration/checkpoint.py:104` (SQLite,
   live) and `orchestration/managers/checkpoint.py` (`FileCheckpointStore`, **zero importers,
   dead**)
10. `max_recursion` / `LimitCounter.recursion` dead
11. `CritiqueCfg.min_tier`/`revise_max` unwired
12. `max_replans` comment contradicts the code
13. DAG + OTAR double execution; DAG bypasses critique/limits/monitor/recorder/events;
    `_run_step`'s `total_steps` param unused
14. Replan parse failure silently returns the original plan while consuming a slot
15. **`DecisionTrace`/`FailureRecord` never written** (`orchestrator.py:286-287` hardcodes
    `()`), despite `DecisionPoint` enumerating all 8 decision sites. Trajectory records the
    **original** plan, not the replanned one. **Phase 37 needs these.**
16. `GoalVerifier` uses `CorrelationId("verification")` — a constant, breaking per-task
    tracing and cost attribution
17. **Five copies of "slice the outermost JSON object"**: `plan_parsing.py:63`,
    `Router._json` :84, `ResponseParser._json` :52, `SelfCritique._json` :217, inline in
    `GoalVerifier` (`goal.py:206`), plus a redundant pre-check at `replanner.py:137-139`. And
    **three** plan deserializers: `plan_from_llm_json`, `resume.plan_from_checkpoint`,
    `resume._step_from`
18. `ObservationRecord.content: str|None` vs `TaskResult.observations: tuple[Any,...]` —
    typed boundary lost at `types.py:102-103`
19. **`SelfCritique.reflect` fires `asyncio.create_task` without holding a reference**
    (`self_critique.py:122-130`) — GC-able, unawaited at shutdown. Phase 20 violation.
20. **`NoOpReflection.reflect` reads the wrong field** — `observation.success` via `hasattr`
    (`reflection.py:49`); `Observation` has `ok` → always returns `succeeded=True`
21. `PlanStep.expected_output`, `Plan.termination_conditions`, `Plan.unknowns`,
    `Plan.estimated_cost_usd`, `Thought.reasoning_details` are parsed and stored but drive **no
    control flow**. Loop termination is purely `action.kind in ("final_answer","ask_user")`
    plus limits — `termination_conditions` is never checked
22. `plan_parsing.py:57-58` `float(str(...))` with no try/except and no clamp; `planner.py:72`
    converts the `ValueError` to `PlanningError` while `replanner.py:142`'s bare
    `except Exception` swallows it into "return original plan" — **divergent behavior for
    identical malformed input, exactly what `plan_parsing.py` exists to prevent**
23. `ContextBuilder.build`'s `plan_summary` param is never passed by any caller → the `plan`
    layer never materializes
24. Trailing-whitespace-only lines at `orchestrator.py:264,273`
