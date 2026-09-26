# Jev in Execution (§32-§34/§64/§78)

Jev (via the Part-3 JudgmentProvider cascade) is used ONLY for bounded
execution judgments — "is this result sufficient?", "retry or replan?" — only
where deterministic checks cannot answer, and only when enabled in config.
Its output passes the risk-dependent threshold policy; low confidence falls
back (deterministic → LLM → human); Jev is never authorization (§31) and
never overrides deterministic rules (§78-tested). Decisions are recorded in
the run state and route events for later calibration (§57-§59).
