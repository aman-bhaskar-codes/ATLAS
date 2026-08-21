# Phase 0 Findings — Model Call Path (Intelligence Layer)

Verified by reading. Every claim has a file:line proof.

## Answer to the Phase 4 question: fast/deep routing does NOT exist

**`fast_model` / `deep_model` / `fallback_model` config keys: NONE, anywhere in the repo.**
No `latency_class`, no `LatencyClass`, no FAST/DEEP enum, no per-tier model list, no `tier`
field on `ModelSpec`/`InferenceRequest`/`Constraints`/`InferenceResponse`/`llm_calls`.

The *entire* existing fast/deep mechanism is one boolean:
- `ModelRequest.needs_deep_reasoning: bool` `infra/types.py:195`
- consumed at exactly one place — `gateway.py:75` → `Constraints(prefer_local=not needs_deep_reasoning)`
- which only affects `local_bonus` (0.15 vs 0.0) at `selector.py:102-103`
- and is **dead whenever** `cost_policy` ∈ {ZERO_COST, FREE_ONLY, FREE_PREFERRED}, because
  `local_bonus` is already 0.25 in that branch

Producers of `needs_deep_reasoning=True`: `planner.py:64`, `reasoning.py:471`
(`plan.confidence < 0.6`), `replanner.py:130`, `consolidation.py:74`,
`experience_extractor.py:136`, `knowledge_platform.py:130`, `cli.py:505` (`--deep`).

`ModelRequest.stakes_tier` `types.py:196` is set by `planner.py:65` and `reasoning.py:472`
but **`gateway.complete()` never reads it**. Dead end.
`ModelTarget` (`LOCAL_FAST/LOCAL_HEAVY/CLOUD`) is **only ever written, never used for routing** —
`gateway.py:90` derives it post-hoc from `resp.usage.usd > 0`, so a free_quota *cloud* model
is mislabelled `LOCAL_FAST`. `ModelRequest.force_target` never read.

**Conclusion: Phase 4 is real work — config schema + selector changes, not a wiring tweak.**

## Contracts (exact)

`intelligence/contracts.py`, all frozen pydantic:
- `ModelSpec` :40-67 — `id, provider, provider_model, context_length, usd_per_1m_input,
  usd_per_1m_output, cost_class, latency_estimate_ms, capabilities, max_concurrency,
  supports_streaming, supports_structured_output, supports_reasoning, supports_vision,
  supports_tool_calling, quality_score, reliability_score, preferred_tasks, enabled`
- `Constraints` :70-88 — `max_latency_ms, max_cost_usd, min_context, require_streaming,
  prefer_local, pinned_model, cost_policy, network_policy, privacy_class`
- `InferenceRequest` :91-101 — `correlation_id, messages, required_capabilities, constraints,
  max_tokens, temperature, stream, task_id, tools`
- `InferenceResponse` :104-115 — `text, model_id, provider, usage, latency_ms, attempts,
  fell_back, truncated, tool_calls, reasoning_details`
- `Usage` :33-37 — `input_tokens, output_tokens, usd`

`infra/types.py`: `ModelCapability` StrEnum, 15 members :68-84. `CostClass` :98
(LOCAL/FREE/FREE_QUOTA/PAID). `CostPolicy` :113 (ZERO_COST/FREE_ONLY/FREE_PREFERRED/
BALANCED/UNRESTRICTED). `NetworkPolicy` :130 (OFFLINE/LOCAL_ONLY/FREE_CLOUD/UNRESTRICTED).
`PrivacyClass` :145 (PUBLIC/INTERNAL/PRIVATE/SENSITIVE/SECRET).

## Call chain, gateway → HTTP

```
gateway.infer()                              gateway.py:48
├─ SemanticCache.get                         :50  → cache.py:65
├─ CapabilityRouter.required                 :54  → router.py:16
├─ ModelSelector.select                      :55  → selector.py:32
│   ├─ CapabilityIndex.candidates            → required.issubset(m.capabilities)
│   ├─ _passes  (hard filters)               selector.py:42
│   └─ _score   (ranking)                    selector.py:89
├─ FallbackEngine.run                        :56  → fallback.py:47
│   └─ InferenceRuntime.attempt              → inference.py:66
│       ├─ ProviderRegistry.get              :67
│       ├─ FreeQuotaGovernor.check           :77
│       ├─ CostGovernor.check                :85
│       ├─ RetryEngine.run(_call)            :113
│       │   └─ Provider.complete             :92
│       │       └─ httpx.post                openai_compatible.py:100  ← EGRESS
│       └─ HealthMonitor/Telemetry/LLMCallTracker  :128-152
└─ SemanticCache.put  (only if not fell_back) :59
```

## Scoring function (verbatim, `selector.py:89-128`)

```
0.30 * quality_score          (static, from YAML)
0.20 * reliability_score      (static — update_reliability never called)
0.15 * 1/(1 + usd_per_1m_output)   (input price ignored)
0.10 * 1/(1 + latency_ms/1000)
0.15 * HealthMonitor.reliability(provider)   (the only live signal)
+ local_bonus   0.25 (free-ish cost_policy) | 0.15 (prefer_local)
+ free_bonus    0.10 (FREE_QUOTA + free-ish policy)
+ privacy_bonus 0.10 (PRIVATE/SENSITIVE + LOCAL)
```
Base weights sum to **0.90**; bonuses reach **+0.45** (max 1.35), so bonuses can outweigh a
0.35 quality gap. Never scored: `max_cost_usd`, `preferred_tasks`, `max_concurrency`,
`context_length` beyond the min filter, all `supports_*` beyond streaming.
`_score` is **not logged**, contradicting the "every filter decision is traceable"
docstring at `selector.py:13-14`.

## P0 — Policy enforcement is DEAD at runtime

`selector.py:53-85` implements every cost/network/privacy filter correctly. But
`Constraints` defaults are `UNRESTRICTED/UNRESTRICTED/PUBLIC` (`contracts.py:86-88`), and
**every** `Constraints(` construction in `src/` passes *only* `prefer_local`:
`gateway.py:75` plus 30+ `agents/*` call sites. Nothing in `src/` ever sets `cost_policy`,
`network_policy`, or `privacy_class`. Only `tests/free_first/test_free_first_invariants.py`
does.

`bootstrap/intelligence.py:81-88` merely **logs** the profile policies. So the docstring
claim at `contracts.py:73-74` ("injected from the active profile") is false.

The only thing actually keeping paid models out is **provider registration**
(`bootstrap/intelligence.py:139` gates on `profile.allow_cloud`) plus `enabled: false` in
YAML. **Phase 5 must wire profile → Constraints.**

## P0 — `nemotron-3.5-lightning` is paid + enabled

`models.yaml:369-390` — `cost_class: paid`, `enabled: true`, `quality_score: 0.90` (highest
among enabled). Under any profile that registers `openrouter` it **wins the ranking**, then
trips `CostGovernor.check` with `daily_usd=0.0` → `BudgetExceededError`
(`cost_governor.py:23`), which is `retryable=False, provider_switch_helps=False`
(`errors.py:32`) → `FallbackEngine` **breaks the chain** at `fallback.py:58`. Result: hard
failure instead of falling back to a free model. Most likely production breakage in the
selection path.

## P0 — Free-quota pre-check falsely exhausts quota on the first call

`InferenceRuntime._estimate_tokens` `inference.py:179-182` returns
`approx_in + spec.context_length // 4`. Using **context window** as a token estimate is a
category error (`max_tokens` was presumably intended).

- `openrouter-deepseek-v4-flash`: `context_length: 1048576` → ≥262,144 estimated tokens
  vs OpenRouter `daily_tokens=200_000` (`bootstrap/intelligence.py:128-130`)
- `gemini-2.0-flash`: ctx 1,048,576 vs `daily_tokens=1_000_000` (:126)

`quota_governor.py:110` → `QuotaExhaustedError` **before any real usage**.

## P0 — A streaming request can never route

`CapabilityRouter.required` adds `Capability.STREAMING` when `req.stream` (`router.py:18-19`),
but **not one of the 15 models lists `streaming` under `capabilities:`** — they only set the
separate `supports_streaming: true` flag. `CapabilityIndex` uses
`required.issubset(m.capabilities)` → `RoutingError` at `selector.py:38`.

Relatedly, `runtime/streaming.py:23` `StreamingRuntime` is **never imported or instantiated**
anywhere; `ModelGateway` has no `stream()` method. `streaming.py:71` also hardcodes
`approx_out = 0`.

## P1 — Observability gaps (Phase 4 requires these)

`llm_calls` table (`infra/db.py:225-240`), writer `llm_tracker.py:26`, called
`inference.py:140-152`:
- `step_index` — **column and parameter exist, never passed** → always NULL
- `cached` — never passed → always 0; and the cache hit at `gateway.py:51` returns *before*
  any tracker write, so `cache_hit_rate()` structurally always returns 0.0
- **failures never recorded** — the `except` branches `inference.py:114-125` do not call the
  tracker, so failures/retries/quota rejections/fallbacks are invisible
- no tier, no `cost_class`, no `attempts`/`fell_back`; `task_id` receives the **correlation
  id** (`inference.py:143`) because `gateway.complete()` never sets `InferenceRequest.task_id`

`audit_events`: `Telemetry.record_failure` writes **`outcome="ok"`** (`telemetry.py:26-27`
uses the same hook) → the audit ledger cannot distinguish success from failure, and it is the
same ledger `CostGovernor` reads.

Phase 4 requires provider/model/tier/latency/tokens/success/task_id/step_id. Today:
tier ❌, success ❌, step_id ❌, task_id ⚠️ (conflated).

## P1 — Provider isolation violations

- `atlas_cli/main.py:642-643, 676-682` — CLI imports `openrouter_free` directly and mutates
  the live `ModelRegistry`
- `interfaces/api/routes_providers.py:20-22, 72` — `atlas.gateway._runtime._providers`,
  `._health`, `._quota` (three levels of private reach-through)
- `interfaces/api/routes_ops.py:117-121` — same pattern
- `routes_providers.py:79-86` — **a second, independent `models.yaml` parser**
  (`Path(__file__).parents[4] / "config" / "models.yaml"`), bypassing `ModelRegistry`; will
  disagree with it (never sees synced specs or `disable()` calls)

**`importlinter.ini:37-43` `provider-sdk-containment` is VACUOUS** — its `forbidden_modules`
is `atlas.infra.providers`, a package that **does not exist** (providers live at
`atlas.intelligence.providers`). The contract enforces nothing. `source_modules` is only
`atlas.safety, atlas.tools`, so `interfaces` and `atlas_cli` are uncovered anyway.

## OpenRouter specifics

- Key env var: **`OPENROUTER_API_KEY`** — no `ATLAS_` prefix, via explicit
  `validation_alias` `infra/config.py:41`. Present in `.env`, **absent from `.env.example`**.
- Base URL **hardcoded** in the provider: `openai_compatible.py:29-33` sets
  `self._is_openrouter = api_key.startswith("sk-or-v1-")` and overrides `base_url`. Both
  `_map_model` (:36-45) and `payload["reasoning"] = {"enabled": True}` (:71-72) are keyed off
  that prefix → **a key in any other format silently disables reasoning capture**.
- `openrouter_free.py` is **not a Provider** — pure catalog discovery
  (`GET /models`, `_is_free` requires prompt and completion price == 0.0). All inference goes
  through `OpenAICompatibleProvider` registered as `"openrouter"`.
- **Synced models are unroutable**: `openrouter_sync._to_spec:22-26` grants only
  `{SUMMARIZATION, CLASSIFICATION}` (+TOOL_CALLING/VISION if advertised). `REASONING` is
  never granted, and `CapabilityRouter` defaults to `{REASONING}` (`router.py:25`) → no
  dynamically-synced model can ever satisfy a default request.
- Startup-only sync (`bootstrap/intelligence.py:208-216`); **no scheduled re-sync**.

## config/models.yaml — 15 models

Enabled: `qwen3-4b` (ollama/local), `openrouter-gpt-oss-20b`, `openrouter-nemotron-3-nano`,
`openrouter-deepseek-v4-flash`, `openrouter-gemma-4-31b-it`, `openrouter-gemma-4-26b`,
`openrouter-free-fallback` (all free_quota), and `nemotron-3.5-lightning` (**paid**).
Disabled: `gemini-2.0-flash`, both groq, `glm-5.2`, `deepseek-v4-pro`, `kimi-k2.7-code`,
`mimo-v2.5-pro`.

Contradiction: `gemini-2.0-flash` lists `tool_calling` in `capabilities` while
`supports_tool_calling: false` (:52 vs :64) — the selector matches on `capabilities`, so it
would be picked for a tools request.

`config/settings.yaml` `models:` block → `ModelCfg` `infra/config.py:62-71`. Only
`local_timeout_s`/`cloud_timeout_s` reach the intelligence layer. `allow_cloud` and the four
`*_usd` fields are **shadowed** by `profile.*` (`bootstrap/intelligence.py:110-115, 139`).
`gpu_concurrency` read by nothing. Top-level `profile:`/`cost_policy:`/`network_policy:` keys
in `settings.yaml:8,13-14` are **read by nothing** — `AppConfig` has no such fields and
pydantic `extra="ignore"` drops them silently at `config.py:166`.

## Other dead / broken

- `InferenceRuntime._timeout_s` set (`inference.py:52,61`) and **never used** — no
  `asyncio.wait_for` in `attempt()`. `InferenceTimeoutError` never raised.
- `governance/rate_limiter.py` `TokenBucket` never imported. `inference.py:3` docstring
  claims a rate-limit step that does not exist. `ProfileConfig.enable_rate_limiting` read by
  nothing.
- `prompt/compiler.py` + `blocks.py` + `templates.py` **dead** — zero references; every
  caller hand-assembles messages inline. `compiler.py:34` would also let `TASK` (=7) be
  trimmed, i.e. drop the user's task.
- Semantic-cache TTL branch unreachable: `cache.py:112-114` intersects
  `ModelCapability` values with the literal set `{"calendar","mail","web",...}` — no overlap
  possible → **every** cached response gets a 1-day TTL, including calendar/mail.
- `routes_providers.py:31` calls `health.latency(name)` — **no such method** on
  `HealthMonitor` → `AttributeError`. `providers_free` (:41-45) calls it too, so it fails too.
- `FreeQuotaGovernor` persistence is a stub: `set_db()` :79-81 stores `self._db`, never uses
  it, and is never called. Docstring claim of SQLite durability is false — all quota state is
  in-memory, resets every restart. `QuotaState.is_daily_exhausted` :47-49 is
  `return False`. `reset_daily()` :162 has no scheduler caller.
- `provider.health()` **never called** — `gateway.health()` reports circuit-breaker state,
  not liveness. `HealthMonitor._breakers` is a `defaultdict`, so it reports `True` for any
  provider never called (including unregistered ones).
- `Constraints.pinned_model` never populated; `selector.py:36`'s `or candidates` silently
  ignores an unsatisfiable pin.
- `FallbackEvent(correlation_id="fallback", ...)` `fallback.py:69-75` hardcodes a literal
  string — fallback events cannot be correlated to a task.
- Cache write skipped when `resp.fell_back` (`gateway.py:58`) → fallback path re-pays forever.
- `.env` sets `ATLAS_DEEPSEEK_API_KEY`/`ATLAS_GLM_API_KEY`/`ATLAS_KIMI_API_KEY`/
  `ATLAS_MIMO_API_KEY` — **none exist as `Settings` fields**, silently dropped.
- Doc drift: `gateway.py:6` and `contracts.py:5` reference `complete_legacy()`, which does
  not exist (real name `complete()`).
