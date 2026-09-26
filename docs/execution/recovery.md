# Recovery (§63-§67)

`RecoveryController.decide()` receives failure class, step, fallback chain,
budgets, and candidate metadata, and returns RETRY / FALLBACK / REPLAN / HUMAN
/ SKIP / TERMINATE — it NEVER executes the decision. Deterministic
classification decides first; the judgment cascade (Jev/LLM) is consulted only
for ambiguous classes and only when enabled, and its answer is still bounded
by policy, budgets, and side-effect rules (§33/§64). Before ANY recovery
applies, policy/budget/candidate availability are re-checked (§67). AUTH
failures escalate to humans (§52) — replanning cannot fix missing credentials.
