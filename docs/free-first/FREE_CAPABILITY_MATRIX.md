# ATLAS Free Capability Matrix

What ATLAS can do at $0, generated from the actual code base (not aspiration).
Sources: `config/models.yaml`, `src/atlas/intelligence/selection/selector.py`,
`src/atlas/infra/profiles.py`, `src/atlas/capabilities/**`.

| Capability    | Local (default)              | Free cloud (optional)        | Paid (optional) | Fallback     |
| ------------- | ---------------------------- | ---------------------------- | --------------- | ------------ |
| LLM reasoning | Ollama (`qwen3:4b`, etc.)    | Gemini free tier, Groq free tier, OpenRouter free models | OpenAI-compatible paid models (blocked under `zero_cost`) | Local model  |
| Classification / routing | Local small model | Groq (fast free tier) | — | Local model |
| Vision        | Local vision model (if configured) | Gemini free tier | paid models | Disabled (explicit) |
| Embeddings    | Local embedding model        | Free API providers (optional)| paid           | Explicit failure — never silent zero vectors |
| Reranking     | Local semantic similarity / lexical / RRF | optional free cloud reranker | optional | Lexical / RRF |
| Web search    | Local cache + official sources | Free search providers where verified | optional | Cache + memory (ATLAS states browsing is unavailable rather than hallucinating) |
| Knowledge     | Wikipedia, arXiv, Crossref, OpenAlex, RSS (`config/knowledge_sources.yaml`) | GitHub public API | — | Local cache |
| Memory        | SQLite + Chroma + local embeddings | — | — | SQLite |
| Queue / bus   | SQLite-backed MessageBus     | — | Redis (production profile) | SQLite bus |
| Storage       | SQLite + filesystem          | — | Supabase / object storage | Local filesystem |
| Browser       | Playwright (headless, local) | — | — | Disabled (explicit) |
| Email/Calendar/Contacts | Local providers where configured | Gmail/Google APIs (user account) | — | Disabled (explicit) |
| Notifications | Terminal, desktop, ntfy      | — | — | Terminal |
| Observability | OTel + Prometheus + structured logs (local) | — | Sentry (free dev tier, optional) | Local logging |
| Evaluation    | Deterministic tests + local judges | — | — | Deterministic only |

## Cost classes

Every model in `config/models.yaml` declares `cost_class`:

- `local` — Ollama, $0, no key, no network
- `free` — permanently free endpoint (rare; must be verified)
- `free_quota` — $0 within provider quota (Gemini, Groq, OpenRouter free)
- `paid` — costs money; hard-blocked when `cost_policy: zero_cost`

## Current model registry (config/models.yaml)

| Model id            | Provider     | Cost class   |
| ------------------- | ------------ | ------------ |
| qwen3-4b            | ollama       | local        |
| gemini-2.0-flash    | gemini       | free_quota   |
| groq-llama-3.3-70b  | groq         | free_quota   |
| groq-llama-3.1-8b   | groq         | free_quota   |
| glm-5.2             | openrouter   | paid*        |
| deepseek-v4-pro     | openrouter   | paid         |
| kimi-k2.7-code      | openrouter   | paid         |
| mimo-v2.5-pro       | openrouter   | paid         |

*Free OpenRouter models are discovered dynamically with `last_verified`
timestamps; no free model is assumed permanent.
