# CURRENT TRAJECTORY STATE (Prompt 4 Phase 0 audit)

## What records trajectories today

`memory/trajectory.py` defines frozen pydantic models; `memory/trajectory_store.py`
persists them to SQLite (tables: `trajectories`, `decision_traces`,
`failure_records`, `experiences`).

### Trajectory fields (existing)
Identity (`id`, `task_id`, `correlation_id`), request/goal/plan_steps,
risk_level, plan_confidence, `actions` (ActionRecord), `observations`
(ObservationRecord), decision/failure ID lists, replan_count,
verification_passed/score, success/answer/error/steps_taken, latency_ms,
tokens_used, cost_usd, model_calls, tool_calls, created/completed timestamps.

### DecisionTrace (existing)
`DecisionPoint` enum: ROUTING, PLANNING, MODEL_SELECTION, TOOL_SELECTION,
REPLANNING, VERIFICATION, SAFETY_TIER, CRITIQUE.
Fields: options_considered, chosen_option, rationale, context, outcome
(SUCCESS/FAILURE/SUBOPTIMAL/UNKNOWN), confidence, latency_ms, cost_usd.
Structured metadata only — never raw chain-of-thought (spec §3 compliant).

### FailureRecord (existing)
`FailureCategory`: TOOL_ERROR, MODEL_ERROR, PLANNING_ERROR, VERIFICATION_FAILED,
TIMEOUT, CANCELLATION, SAFETY_BLOCK, RESOURCE_EXHAUSTION, UNKNOWN.
Fields include recovery method/success and pattern-detection hooks
(similar_failure_ids, mitigation_suggested/applied).

### Store capabilities
`save_trajectory`, queries by task/success/replans/latency/category/time range,
`get_failed_trajectories`, decision trace save/update-outcome, failure records
with `get_failure_patterns` grouping, experience CRUD with application tracking.

### API exposure
`interfaces/api/routes_trajectory.py` — `DecisionTraceOut` and trajectory
read endpoints already exist.

## Gaps vs Prompt 4 §2

| Required | Status |
|---|---|
| atlas_version / git_commit / config_hash | ❌ missing — reproducibility fields |
| strategy + strategy_version | ❌ missing |
| model_version / capability_snapshot_version | ❌ missing |
| safety events list | ❌ only SAFETY_TIER decision point |
| memory usage / knowledge retrieval / browser research links | ❌ missing |
| confidence at completion (for calibration) | ⚠️ plan_confidence only |
| 23-class failure taxonomy (§6) | ⚠️ only 9 coarse categories |
| root-cause vs symptom analysis | ❌ none |
| deterministic clustering | ⚠️ only simple grouping query |

Prompt 4 extends `Trajectory`/`DecisionTrace`/`FailureRecord` with additive
optional fields (frozen models stay backward compatible) and adds the
adaptation-level taxonomy + analyzer on top — no duplicate trajectory store.
