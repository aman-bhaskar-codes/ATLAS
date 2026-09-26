# Fallback (§29-§31)

Fallbacks are the Part-3 decision's ranked runner-ups. On a non-retryable (or
budget-exhausted) failure the controller picks the next candidate and the
engine re-validates it against the CURRENT catalog (exists + READY) before
running it; a stale/dead candidate is skipped and the cursor advances. Context,
constraints, policy, and budgets are preserved — only the candidate changes.
The world may have changed since routing: every step ALSO revalidates its
candidate pre-execution, so a candidate that vanished after routing fails fast
into fallback instead of erroring obscurely (§31/§77-tested).
