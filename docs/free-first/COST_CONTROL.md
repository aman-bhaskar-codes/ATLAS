# Cost Control

Implemented in `src/atlas/intelligence/governance/` and surfaced via the
`atlas cost` CLI and the `/cost` frontend page.

## Layers

1. **Selection-time exclusion** (`selection/selector.py`): `zero_cost` and
   `free_only` remove paid models from the candidate list before any request.
2. **Budget governor** (`budget.py`, `cost_governor.py`): USD budgets per
   day/week/month/task (`config/settings.yaml: models.daily_usd` etc.). When a
   budget is hit, remaining work routes to local models.
3. **Quota governor** (`quota_governor.py`): per-provider free-tier
   request/token counters; approaching a limit → stop → fallback → local.
4. **Rate limiter** (`rate_limiter.py`): respects provider RPM/TPM limits.
5. **LLM tracker** (`infra/llm_tracker.py`): records requests, tokens, and
   estimated cost per provider per task — the data behind `atlas cost` and
   the cost dashboard.

## CLI

```bash
atlas cost                    # current spend + policy
atlas cost enforce zero_cost  # hard-block paid providers
```

## Invariant (tested in tests/free_first/)

`ATLAS_COST_POLICY=zero_cost` ⇒ paid provider calls = 0, even when paid API
keys are configured. Estimated cost of local inference is $0 by definition
and displayed as such.
