# Execution Lifecycle (§7-§8/§93)

Run states: CREATED → VALIDATING → READY → RUNNING → {WAITING_DEPENDENCY,
WAITING_CONFIRMATION, WAITING_HUMAN, RETRYING, FALLING_BACK, REPLANNING,
PAUSED, CANCELLING} → COMPLETED | FAILED | CANCELLED. Transitions are a typed
table (`transition_run`); an illegal transition raises immediately (§7). A
FAILED run can be re-armed by `retry(run_id)` — the one legal exit from FAILED.

Step states: PENDING → READY → RUNNING → SUCCEEDED | FAILED | RETRYING |
FALLBACK | WAITING | CANCELLED, plus SKIPPED/BLOCKED for DAG effects (§8).

Terminal outcomes (§93): SUCCESS, PARTIAL_SUCCESS (failed optional branches,
§59), FAILED, CANCELLED, BLOCKED (invalid plan), WAITING_HUMAN (resumable),
DEAD_END (structural, §61), BUDGET_EXCEEDED. Completion is a POLICY question —
never "no runnable steps" (§60).
