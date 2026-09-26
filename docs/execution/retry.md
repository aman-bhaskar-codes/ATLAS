# Retry (§25-§28)

`classify_result`/`classify_exception` map failures to RetryClass (TIMEOUT,
RATE_LIMIT, NETWORK, TRANSIENT, AUTH, POLICY, INVALID_INPUT, SCHEMA, ...).
Retry eligibility = class ∈ retryable ∧ attempts < max ∧ SIDE-EFFECT check:
a non-idempotent side-effecting step is NEVER auto-retried — it becomes
SIDE_EFFECT_UNCERTAIN and routes to verification/human (§24/§72/§118-tested).
Policy denial and bad input never retry (§28) — they route to fallback/replan/
human. Backoff: exponential, bounded, jittered (§26); budgets: per-step
attempts from `execution.retry_max_attempts`, global bounds stay in
`ExecutionLimits` (§27).
