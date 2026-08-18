# Environment Profiles

Defined in `src/atlas/infra/profiles.py`. One env var switches the whole
operating posture; orchestration logic never changes across profiles.

```bash
ATLAS_PROFILE=local_free|free_hybrid|free_demo|production
```

| Profile          | Cloud models | Cloud storage | Network policy | Cost policy            | Intended use |
| ---------------- | ------------ | ------------- | -------------- | ---------------------- | ------------ |
| `local_free` (default) | No      | No            | `local_only`   | `zero_cost`            | Development, single user, $0 |
| `free_hybrid`    | Free-tier only (Gemini/Groq/OpenRouter free) | No | `free_cloud` | `zero_cost` or `free_only` | Local + opportunistic free cloud |
| `free_demo`      | Free-tier, rate-limited | Optional (Supabase free) | `free_cloud` | `zero_cost` | Public demos, auto-degradation, rate limiting |
| `production`     | Optional paid | PostgreSQL/Redis/object storage | `unrestricted` | `balanced`/`unrestricted` | Scales by swapping infra implementations |

Overrides (any profile): `ATLAS_COST_POLICY`, `ATLAS_NETWORK_POLICY`.

## Cost policies

`zero_cost` · `free_only` · `free_preferred` · `balanced` · `unrestricted`

Under `zero_cost`, `ModelSelector` excludes every `paid` model at selection
time — an existing API key does not matter; paid routes are unreachable.

## Network policies

`offline` → local tools/models only · `local_only` → no external API ·
`free_cloud` → approved free-tier providers only · `unrestricted`.

Enforced before provider selection (`selector.py`), not after.
