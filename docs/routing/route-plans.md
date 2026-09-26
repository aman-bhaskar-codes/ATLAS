# Route Plans & Graphs (L8)

`RoutePlanner.compile` turns a decision into a `RoutePlan` (steps, fallbacks, budgets
sourced from the existing `ExecutionLimits`, termination/verification conditions) and
`build_graph` into an explicit `RouteGraph` (EXECUTE/DECIDE/PARALLEL/JOIN/VERIFY/REPLAN/
HUMAN_REVIEW/END nodes; success/failure/timeout/low_confidence/verification_failed/
needs_more_information edges). Static templates exist for research and software
engineering (`deep_research_default`, `software_engineering_default`); everything else
compiles dynamically. Cross-domain tasks are explicit steps with `domain` + dependencies.
Every plan is VALIDATED before execution (candidate exists/available, dependencies,
terminal node, termination conditions) — invalid plans are rejected, never run.
