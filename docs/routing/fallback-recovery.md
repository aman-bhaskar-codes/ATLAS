# Fallback & Recovery (L9)

Fallbacks are the ranked runner-up candidates on the decision and appear in the graph as
failure edges (primary →(failure)→ fallback-1 → … → REPLAN). `classify_failure` maps
execution outcomes (tooling `FailureKind`, capability errors, HTTP status) to routing
`FailureCategory` values — extending, not duplicating, the existing taxonomy. Each
category has a recovery ladder (§52); `RecoveryRouter` walks it under HARD bounds
(`max_recovery_retries`/`max_replans`); exhaustion TERMINATES. A SafetyEngine denial is
terminal at this layer — recovery never retries past the SafetyEngine. Route recovery
events (`route.replanned`, `route.fallback_selected`) ride the `route` topic.
