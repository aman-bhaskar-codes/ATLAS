# Routing Evaluation (§60/§85/§87)

Dataset: `eval/routing/routing_benchmark.json` — domain/strategy/fallback/recovery
cases, easy and ambiguous. Harness: `routing/evaluation.py` (`RoutingAblationHarness`)
runs the same cases against configurable stacks — rule_only / rule+jev / rule+llm / full
— and measures accuracy, escalation rate, fallback rate, and latency. Offline ablations
use scripted provider doubles; thresholds are calibrated against these measurements
(§59), not assumed. Route snapshots (persisted decisions incl. judgment + alternatives)
are the RAL data source (§86); `replay(route_id)` reconstructs decisions from the
recorded snapshot without fresh external calls (§67).

Measured fast-path latency (100 routes, judgment rungs off): full route p50 ≈ 0.07 ms,
p99 ≈ 0.2 ms — the router adds sub-millisecond overhead before a task; Jev/LLM rungs
engage only on ambiguous cases.
