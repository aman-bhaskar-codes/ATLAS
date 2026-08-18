# Current Provider Matrix

Implemented adapters in `src/atlas/intelligence/providers/` and their status.

| Provider     | Adapter file                  | Cost class(es)   | Key required | Health check | Quota tracked | Failover |
| ------------ | ----------------------------- | ---------------- | ------------ | ------------ | ------------- | -------- |
| Ollama       | `ollama.py`                   | local            | No           | Yes (`/api/tags`) | n/a       | Terminal fallback |
| Gemini       | `gemini.py`                   | free_quota       | `GEMINI_API_KEY` | Yes       | Yes (`intelligence/governance/quota_governor.py`) | → Groq → Ollama |
| Groq         | `groq.py` (openai-compatible) | free_quota       | `GROQ_API_KEY`   | Yes       | Yes | → Gemini → Ollama |
| OpenRouter   | `openai_compatible.py`        | free_quota / paid | `OPENROUTER_API_KEY` | Yes  | Yes | free models only under `free_only`/`zero_cost` |
| Anthropic    | `anthropic.py`                | paid             | `ANTHROPIC_API_KEY` | Yes     | Yes | blocked under `zero_cost` |

Governance (all providers): `cost_governor.py` (USD budgets), `quota_governor.py`
(free-tier request/token quotas), `rate_limiter.py`, `budget.py`,
`health/health_monitor.py` (latency, error rate, cooldown).

Capability-level providers (email/calendar/contacts/knowledge/MCP) live in
`src/atlas/capabilities/providers/` behind the `ProviderRegistry`
(`capabilities/registry/provider_registry.py`) with `CapabilityHealth`.

Secrets: environment variables only. The frontend/API expose only
configured / missing / invalid — never the key itself.
