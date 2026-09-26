# Jev Integration (§23-§24/§57-§59/§81-§82)

`JevClient` speaks the official TypeSafe API (`POST /v1/systemone` with
`{state, model, questions}`; `choice`/`score` responses; multiple questions per call).
Auth is env-based (`TYPESAFE_API_KEY`, never hard-coded), with timeouts, bounded retries
on 429/5xx/transport errors, defensive response validation (garbage → `JevSchemaError` →
cascade fallback), and latency/call telemetry. The provider is OFF by default
(`routing.enable_jev: false`) — no decorative Jev (§88): it activates only where
deterministic rules are inconclusive. The direct API is the integration path; a
third-party MCP wrapper is NOT a dependency (§82).

Observability (§57): decision id, provider, latency, choice, raw probabilities,
threshold policy, and downstream action land in the persisted RouteDecision + route
events — enough for Jev-vs-outcome calibration (§58) against the routing benchmark
(§59), with secrets never recorded.
