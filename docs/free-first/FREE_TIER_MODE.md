# Free Tier Mode (`free_hybrid`)

Local-first infrastructure plus optional free-tier cloud providers.

## Enabling

```bash
export ATLAS_PROFILE=free_hybrid
export GEMINI_API_KEY=...      # optional
export GROQ_API_KEY=...        # optional
export OPENROUTER_API_KEY=...  # optional
```

Any subset may be configured. Missing keys simply keep that provider disabled;
ATLAS continues on local models.

## Providers and their semantics

| Provider    | Free tier nature                          | Governed by |
| ----------- | ----------------------------------------- | ----------- |
| Gemini      | Per-day free request quota (rate-limit 429s on exhaustion) | `quota_governor`, per-provider daily request caps |
| Groq        | Free tier with RPM/TPM limits             | `rate_limiter` + quota governor |
| OpenRouter  | Free-tagged models, availability changes over time | dynamic discovery with `last_verified`; never assumed permanent |

## Guarantees

- Free tiers are **quota-limited, not unlimited**. The quota governor disables
  a provider temporarily as it approaches its limit and the router falls back
  (Groq → Gemini → Ollama, ordered by health/cost policy).
- No paid model is reachable while `cost_policy` is `zero_cost` or `free_only`.
- Provider selection still respects privacy class: `SECRET`/`SENSITIVE` data
  never routes to cloud providers regardless of quota availability.
- If a free tier disappears, ATLAS keeps operating locally.
