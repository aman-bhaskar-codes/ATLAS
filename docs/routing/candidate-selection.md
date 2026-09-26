# Candidate Selection (L4-L7)

Discovery goes through the Part-2 catalog's `find_candidates` — TOOL candidates today;
the IR carries AGENT/WORKFLOW/MODEL/PROVIDER for when their backing systems register.
The HARD FILTER (`candidates.py`) removes disabled/unavailable/unsupported-operation/
missing-auth/privacy-exceeding/network/cost/trust-violating candidates BEFORE judgment —
property-tested invariants in `test_routing_candidates.py`. Deterministic scoring weights
are config (`routing.weights`); `final_score = deterministic + bounded judgment signal`
with per-component breakdowns recorded on the decision (§34).
