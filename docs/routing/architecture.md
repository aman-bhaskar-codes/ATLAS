# Routing Architecture (Part 3)

> Describes **implemented** behavior. Deferred items are marked.

The routing fabric (`src/atlas/tooling/routing/`) is ATLAS's control plane: it decides
who/what acts, how, with what fallback, and when to escalate or stop. It NEVER executes —
plans reference candidates whose execution flows through the existing governed funnels
(`ToolingExecutor` / dispatchers → `SafetyEngine`).

```
TASK → TaskIR → DOMAIN ROUTER → STRATEGY ROUTER → CAPABILITY DECOMPOSER
  → CANDIDATE SET (Part-2 catalog) → HARD POLICY FILTER
  → JUDGMENT LAYER (deterministic → Jev → LLM) → RANKING → ROUTE PLAN
  → EXECUTION GRAPH → PROGRESS / FAILURE → RECOVERY ROUTER (fallback/replan) → VERIFY → DONE
```

Layers (each a method with a typed contract in `router.py`):

| Layer | Responsibility | Module |
|---|---|---|
| L0 | request normalization → `TaskIR` (source preserved; policy from config, never request text) | `normalize.py` |
| L1 | domain routing cascade: deterministic fast-path rules → judgment → safe default / ask_user | `normalize.py`, `router.py` |
| L2 | strategy routing over the domain's declared strategies | `strategies.py`, `router.py` |
| L3 | capability decomposition, validated against declared capabilities (never invented) | `router.py` |
| L4 | candidate discovery via `catalog.find_candidates` (no raw SQL) | `candidates.py` |
| L5 | hard policy filter BEFORE any judgment (disabled/unavailable/auth/privacy/network/cost/trust) | `candidates.py` |
| L6 | bounded judgment over the top-N survivors only | `judgment.py` |
| L7 | deterministic ranking + bounded judgment composition | `scoring.py` |
| L8 | route plan + execution graph compilation, validation, budgets | `planner.py` |
| L9 | failure classification → recovery graph with hard bounds | `recovery.py` |

Key guarantees: the hard filter runs before any AI judgment; judgment is a signal,
never authorization (the SafetyEngine stays authoritative); identical TaskIR + catalog +
policy + judgment ⇒ identical route; every decision is persisted and replayable.
