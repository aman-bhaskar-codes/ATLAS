# Idempotency & Side-Effect Safety (§22-§24/§56-§58)

Every tool step's invocation carries a deterministic idempotency key
(task_id:run_id:step_id:attempt). Retry eligibility consults the candidate's
declared metadata (idempotent, side_effects) — metadata informs, the
SafetyEngine still authorizes (§85). Non-idempotent side effects
(send/delete/push/pay) are fail-safe: one attempt, then SIDE_EFFECT_UNCERTAIN
routing (verification/human/explicit policy). ATLAS does NOT claim
exactly-once external execution (§82); compensation is a declared hook
(`supports_rollback` on metadata) rather than a Saga engine (§58). Caching is
limited to read-only/pure operations; nothing side-effecting is cached (§56).
