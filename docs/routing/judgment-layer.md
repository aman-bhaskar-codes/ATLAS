# Judgment Layer (§22-§32/§61-§63)

Providers implement `JudgmentProvider` (`judgment.py`): `DeterministicJudgmentProvider`
(exact rules; a question without a rule falls through), `JevJudgmentProvider` (TypeSafe
direct API), `LLMJudgmentProvider` (via the existing ModelGateway — no second model
router). `JudgmentCascade` runs deterministic → Jev → LLM per BATCH of atomic questions;
unavailability/timeout/low-confidence degrades down the ladder, never fails the route.

Invariants: atomic questions only (§25); batching — one provider call per batch (§26);
judgment on the top-N filtered candidates, never the full set (§27/§28); risk-dependent
thresholds via `JudgmentThresholdPolicy` (§29), DESTRUCTIVE decisions never route on
judgment alone; HIGH/MEDIUM/LOW states normalized while raw scores stay in telemetry
(§30); judgment is a signal, never authorization (§31); Jev is NOT used where an exact
deterministic rule, validation, permission check, or budget arithmetic suffices (§61).
