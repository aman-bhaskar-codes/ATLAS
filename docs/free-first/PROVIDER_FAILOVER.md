# Provider Failover

Implemented in `src/atlas/intelligence/gateway.py` + `selection/selector.py`
+ `governance/*`. The core never imports vendor SDKs; it talks to the
`ProviderProtocol` adapters registered in the provider registry.

## Chain

```text
Task (with constraints: capability, privacy, latency, budget)
  ↓
ModelSelector.select() → ranked candidate list (local → free → paid*)
  ↓
ModelGateway.infer() tries candidates in order:
    provider healthy? quota remaining? rate limit ok?
      → call
      → on failure / 429 / quota-exhausted / timeout:
          emit provider.fallback event on MessageBus
          → next candidate
  ↓
all cloud candidates exhausted → Ollama (terminal fallback)
  ↓
no provider at all → explicit, safe failure (never a hang, never silent paid use)
```

*Paid candidates are excluded at selection time under `zero_cost`/`free_only`.

## Provider state (health monitor + governors)

Each provider tracks health, latency, error rate, quota state, last success,
last failure, cooldown, and rate-limit state. Unhealthy / quota-exhausted /
rate-limited / misconfigured providers are skipped before any request.

## Events emitted

`provider.selected`, `provider.failed`, `provider.rate_limited`,
`provider.quota_exhausted`, `provider.fallback`, `provider.recovered` —
published on the MessageBus and recorded in trajectory telemetry. User-facing
messages describe the fallback in plain language ("Groq free quota exhausted —
ATLAS switched to a local model. No paid usage occurred."), never raw HTTP 429s.

## Privacy constraint

Failover never crosses privacy policy: if `privacy_class` requires local-only,
cloud candidates are not in the list at all, so no failure can silently route
around the policy.
