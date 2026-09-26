# Progress Ledger, Stall & Loop Detection (§48-§50)

`ProgressLedger` tracks subgoals (in_progress/completed/blocked/failed, attempt counts,
last-progress timestamp). `StallDetector` flags identical actions repeated, the same
failure repeated, and subgoals reopened beyond bounds. `LoopDetector` detects cycles in
the action sequence (A→B→C→A) once the cycle repeats `max_cycle_repeats` times. All
bounds are config (`routing.*`); recovery cannot exceed them (§84).
