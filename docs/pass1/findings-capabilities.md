# Phase 0 Findings — Capability & Tool Execution Path

> **One agent claim rejected.** Agent 2 reported `safety/engine.py:256`
> `secrets.randbelow(10000)` as an `AttributeError` that would break every Tier-3 confirmation.
> **Verified false** — `secrets.randbelow` is a public, documented stdlib function
> (`'randbelow' in secrets.__all__` → `True`; ran it, returns fine). `engine.py:256` is correct.
> Everything else below was spot-checked against the quoted file:line.

## P0 — There is NO canonical execution path. There are 5; two are live, one is unguarded.

| Path | Route | Guarded? | Live? |
|---|---|---|---|
| **1. OTAR / reasoning loop** | `Orchestrator` → `Router` → `Planner` → `DagExecutor` \| `ReasoningLoop` → `ToolDispatcher.dispatch` → `SafetyEngine.guard` → `tool.execute` | ✅ `dispatcher.py:50` | ✅ **the only real production path** |
| **2. CLI direct-to-guard** | `cli.py:186/213/487` hand-build `ToolRequest`, call `atlas.safety.guard` directly — bypasses registry, dispatcher, router, planner | ✅ | ✅ |
| **3. CapabilityDispatcher** | `CapabilityRequest` → `registry.get` → `providers.candidates` → `_CapabilityTool` → `guard` → `_walk_providers` | ✅ `dispatcher.py:103` | ❌ **ZERO callers in src/** |
| **4. Platform direct-call** | `cli.py:522` → `knowledge_platform.obtain_knowledge` → `provider.search()`; `cli.py:752` calendar; `:803` contacts | ❌ **NO GUARD** | ✅ **LIVE** |
| **5. `DynamicToolOrchestrator`** | `agents/tool_orchestration.py:685` `await tool.execute(args)` after `self._registry.get(...)` :668 | ❌ **NO GUARD** | ❌ unwired (nothing imports `atlas.agents`) |

**Complete `guard()` call-site inventory in all of `src/`** — only 5 sites:
`capabilities/dispatcher.py:103` (dead), `orchestration/dispatcher.py:50` (live),
`interfaces/cli.py:186`, `:213`, `:487`.

**Path 4 is the Phase 31 violation that already exists.** `knowledge_platform.py:8-9` claims
"Every provider fetch goes through the capability dispatcher (Safety Engine)" — `_safe_search`
calls `provider.search(...)` directly at `:91`. Same for calendar/contacts/weather/location/
currency platforms and `capabilities/browser/engines/*`.

## P0 — The planner is told a WRONG tool catalog, and it's the only thing it gets

`orchestrator.py:131` passes `self._registry.catalog()` — the **full, unfiltered, unranked**
list, verbatim, on every task. No filtering by `caps`, no ranking, no truncation. There is no
second injection point (`prompt_builder.py:22` reuses the same string; `Planner.plan` never sees
the registry).

**And the operations are fabricated.** `bootstrap/orchestration.py:86` defines ONE shared tuple
for every tool:
```python
_operations = ("read", "write", "delete", "side_effect", "read_only")
for t in tools.values():
    tool_registry.register(t, _operations, _metadata_map.get(t.name))   # :105-106
```
So the model is told:
- `filesystem: read, write, delete, side_effect, read_only` — **missing the real `search`**
  (which exists at `filesystem.py:71` with a Tier-0 rule at `permissions.yaml:29`)
- `shell: read, write, delete, side_effect, read_only` — only `read_only`/`side_effect` are real
- `browser` (if enabled): all five are **fictional** — `BrowserTool.execute` accepts only
  `research|goto|extract|click` (`browser.py:60-108`)

**Net effect:** `filesystem.side_effect`, `filesystem.read_only`, `shell.read`, `shell.write`,
`shell.delete` are advertised to the LLM and have **no manifest rule** → guaranteed
`deny-by-default` when the model picks one. Meanwhile the one operation that works
(`filesystem.search`) is hidden.

The catalog layer is also **trimmable** — `ContextLayer("tools", ..., 3)`
(`context_builder.py:60`) and `:76` drops priority > 2 on budget overflow. Under a tight 3000-token
budget the tool list is silently dropped while `system`/`safety`/`user_model` survive.

## P0 — `ToolRouter` is the tool-selection component and it selects nothing

Constructed at `bootstrap/orchestration.py:152`, stored `Atlas.tool_router` (`app.py:579`).
**`rank()` and `catalog()` have zero src callers.** Its only production use is as an attribute
bag for `routes_ops.py:70-71`, which reaches into private `_registry`/`_health`. And
`rank()` throws away its only argument — `tool_routing.py:76-77` `del intent`.

**Tool selection is 100% the LLM guessing from a malformed catalog string.** Phase 8 is
greenfield.

## P0 — `mass_deletion` hard-block is unreachable on the production path

`classifier.py:152` requires `req.args["target_count"]`. The **only writer is
`interfaces/cli.py:180`**. `ToolDispatcher` builds args as
`{"operation": action.operation, **action.args}` (`dispatcher.py:42`) and never computes it.
`FilesystemTool._count_delete_targets` (`filesystem.py:53`) is consumed only by `dry_run` (:43)
— which runs **after** classification.

So `filesystem.delete` through the reasoning loop is Tier-2 confirm, **never hard-blocked,
regardless of file count**. `filesystem.py:3-6` claims the opposite.

## The Tool protocol — the entire contract

`tools/base.py:13-18`:
```python
@runtime_checkable
class Tool(Protocol):
    name: str
    def dry_run(self, args: dict[str, Any]) -> str: ...
    async def execute(self, args: dict[str, Any]) -> ToolResult: ...
```
One attribute, two methods. `dry_run` returns a **free-form human string**, called at exactly
one place — `engine.py:250`, only inside `_confirm`, only when tier ≥ 2. **It is not a
simulate API**; no dry-run of a whole execution exists.

**No schemas.** JSON schema is synthesized *externally* at `registry.py:80-83` from the
operations tuple: `{"operation": {"enum": [...]}, "args": {"type": "object"}}` — `args` is an
untyped blob. **Operations are not declared by the tool** — they are passed in by whoever calls
`register()`. **Risk is not declared by the tool** — tier comes only from `permissions.yaml`.

`ToolRequest.declared_tier_hint` (`infra/types.py:42`) can only RAISE tier
(`classifier.py:64-65`) and **no caller in src/ ever sets it**.

## Phase 6 gap analysis — the universal Capability contract

`CapabilitySpec` (`registry/capability.py:36-48`): `capability, version=1, description,
safety_tool, operations, default_tier=NOTIFY, requires_auth, dependencies`.
`ToolMetadata` (`orchestration/registry.py:20-31`): `name, description, operations, safety_tool,
estimated_cost_usd, estimated_latency_ms, idempotent, side_effects, supports_rollback`.

| Phase 6 field | Exists? |
|---|---|
| name, description | ✅ both |
| operations | ✅ but caller-supplied, and wrong (above) |
| **input_schema** | ❌ synthesized externally; `args` untyped |
| **output_schema** | ❌ nowhere |
| **permissions** | ❌ not a field — externalized as the `safety_tool`+`operation` join key into `permissions.yaml` |
| risk | ⚠️ `CapabilitySpec.default_tier` exists but is explicitly non-authoritative **and never read anywhere in src/** |
| **latency_class** | ❌ — numeric proxy `estimated_latency_ms=500` only |
| **cost_class** | ❌ — numeric proxy `estimated_cost_usd=0.0` only |
| **privacy_class** | ❌ **zero occurrences repo-wide** on the capability side |
| reversible | ⚠️ per-result only (`SideEffect.reversible`, post-hoc); declared proxy `supports_rollback` |
| health | ✅ live only, never declared |
| provider | ✅ capability side (`Provider` protocol `providers/base.py:42-54`); ❌ tool side |
| version, dependencies | declared, **never read** |

## Phase 19 — nothing can answer "what can you do right now?"

1. **`GET /api/v1/capabilities`** → `facade.py:229-241` — **values are fabricated**:
   `state="ready"`, `providers=1`, `healthy_providers=1`, `requires_auth=False` hardcoded. The
   last **overrides `spec.requires_auth=True`** for email/calendar/contacts. Never consults
   `cap_providers` or `cap_health`.
2. **`GET /api/v1/providers/health`** → `routes_capabilities.py:20` — a stub returning
   `runtime_health()` (comment: "Phase 1: reusing runtime_health for now").
3. **`GET /api/v1/ops/tools`** → `routes_ops.py:67` — **the only honest surface**, but it sees
   only the 2-3 `Tool` objects, and reaches through private attrs.
4. `CapabilityHealth.snapshot()`, `ProviderRegistry.all_providers()`,
   `CapabilityRegistry.registered_tools()`, `ToolRouter.catalog()` — all exist, **zero callers**.
5. `RuntimeSupervisor._check_capability_health` (`bootstrap/runtime.py:559-573`) only ever checks
   `browser_platform`. Knowledge/email/calendar/contacts/weather/location/currency are **never
   health-checked**.

No `describe`, no `available`, no per-capability probe.

## Deny-by-default: CONFIRMED, with two real caveats

`classifier.py:71-75` — fall-through returns `decision="deny", tier=CONFIRM,
reason="deny-by-default: no manifest rule matched"`. Fail-closed on internal error too
(`:41-47` → `require_confirm` at `default_tier_on_error: 2`).

Caveats:
- `CapabilitySpec.default_tier = Tier.NOTIFY` is a **more permissive contradictory default**
  sitting in the capability layer. Inert today (never read), but it must not be wired naively.
- Deny-by-default holds only on Paths 1-3. On Paths 4-5 "no rule" means **unpoliced**, not denied.
- Drift detection is broken: `verify_manifest` (`manifest.py:67`) would catch registered-vs-manifest
  drift, but `doctor.py:61` feeds it the **hardcoded** `_REGISTERED_TOOLS` (`:31-34`) instead of
  the live registry, **and `run_doctor` never surfaces `report.missing_rules`** (`:62-73` reports
  only `matcher_gaps`, `unmatched_constraints`, `orphan_rules`).

## `config/permissions.yaml` — 50 rules, ordering semantics

Classification order (`classifier.py:49-75`), strictly:
1. `_hard_block` **first** → `deny`/`Tier.BLOCK`, returns immediately. Wins over every rule.
   4 entries: `*.*.credential_access`, `*.*.financial_transaction`,
   `filesystem.delete.mass_deletion`, `*.*.edit_safety_config`.
2. `_required_confirmation` computed (10 matchers, all `tier: 2`, `ge=2` enforced by
   `manifest.py:36`).
3. **First matching rule wins** — `fnmatch` on both tool and operation, **file order is
   authoritative, no specificity sort**. A `{tool: "*", operation: "*"}` rule inserted at the top
   would shadow all 50.
4. **Tightening-only invariant**: `_apply_constraint` does `max(tier, CONFIRM)` (:181);
   require_confirm does `max(tier, required_tier)` (:61); `declared_tier_hint` does `max(...)`
   (:65). Stated at `permissions.yaml:96`: "Matchers may raise a lower base rule to the
   configured tier, never lower it."
5. Decision mapping (`:219-221`): tier ≤ 1 → `allow`; 2-3 → `require_confirm`; 4 → `deny`.
6. `PolicyEngine.evaluate` — only `KillSwitchPolicy` installed (`bootstrap/safety.py:38`).

**Two constraint matchers are stubs that always pass**: `recipients_known` and
`attendees_known` (`classifier.py:194-196`, `return True, "ok"`, comment "Dummy
implementations"). They guard `email.send` (`permissions.yaml:51`) and `calendar.create` (:57)
— so those constraints **never raise tier**. They still pass `verify_manifest` because they're
listed in `KNOWN_CONSTRAINTS` (`:27-28`).

## Capabilities that exist but CANNOT be reached (each verified)

Registered in `CapabilityRegistry`: 7 — knowledge, weather, location, currency, email, contacts,
calendar. In the `Capability` enum with **no spec**: 5 — `BROWSER`, `NOTIFICATION`,
`CLOUD_STORAGE`, `GITHUB`, `FILES` → `get()` raises `CapabilityNotFound`.

**Registered in `cap_providers`: knowledge ONLY** — the sole `register` call is
`bootstrap/capabilities.py:164`. So for weather/location/currency/email/contacts/calendar,
`candidates()` raises `NoProviderAvailable`. **Even if Path 3 were wired, 6 of 7 capabilities
would fail at `dispatcher.py:84`.**

1. `CapabilityDispatcher` + everything behind it — no caller. All 7 specs and all provider
   preference ranking are cosmetic.
2. `CapabilityRouter` (`capabilities/router.py`) — no caller. Its `_SIGNALS` map (:22-31) also
   advertises `GITHUB`/`FILES` (no spec, no provider) and omits `CONTACTS`/`CURRENCY`/
   `CLOUD_STORAGE`, while `_CLASSIFY_SYSTEM` (:33-37) lists 11 names.
3. **`BrowserTool` — disabled by config.** `BrowserCfg.enabled = False` (`infra/config.py:126`)
   and **`config/settings.yaml` has no `browser:` section at all**. So `browser_platform is
   None`, `tools` has no `"browser"` key. All 9 `browser.*` rules and all 4 browser
   `require_confirm` matchers are dead. **This is the Phase 18 test case** — "browser unavailable
   → do NOT fail immediately if another strategy exists."
4. `AppControlTool` (`control/tool.py:22`) — never constructed in src/, not in `app.py:429
   tools`, **and no `app_control` rule in `permissions.yaml`** → triple-unreachable.
5. `AXPerceptionTool` (`perception/tool.py:16`) — same three failures.
6. `weather_platform`, `location_platform`, `currency_platform`, `email_platform` —
   constructed, stored on `Atlas`, **referenced by nothing** outside `app.py`/`bootstrap`.
7. `knowledge_platform`, `calendar_platform`, `contacts_platform` — reachable **only from the
   CLI**, and not through `guard()`.
8. **Browser mutating engines are mocked.** `builder.py:87-90` passes `dispatcher=None` to
   `ClickEngine`/`TypeEngine`/`SubmitEngine`; the dispatch line is commented out in all three
   (`submit.py:60`, `click.py:21`, `type.py:21`). `submit.py:59`: `# Mocking dispatch behavior
   for now`, followed by an unconditional `ActionResult(ok=True, ...)`. Only `SubmitEngine` has
   an out-of-band `request_approval` gate (:48); **click/type have no gate at all.**
9. **Manifest rules with no implementation anywhere**: `code.execute`, `http.get/post/put/delete`,
   `memory.search/store`, `notify.send`, `database.drop/truncate`, `system.config`, `payment.*`,
   `filesystem.overwrite`. No `Tool` named `code`, `http`, `memory`, `notify`, `database`,
   `system`, or `payment` exists.
10. `DynamicToolOrchestrator` — nothing outside `src/atlas/agents/` imports `atlas.agents`.

## Name collisions (Phase 43 item 5 must resolve these)

- **Three classes named `ProviderRegistry`**: `capabilities/registry/provider_registry.py:17`,
  `capabilities/browser/registry/provider_registry.py:10`,
  `intelligence/registry/provider_registry.py:10`. `app.py:32` aliases as `CapProviderRegistry`.
- **Two classes named `CapabilityRouter`**: `capabilities/router.py:40` (dead),
  `intelligence/selection/router.py:15` (live, models). `app.py:33` aliases as
  `ExtCapabilityRouter`.
- **Four routers with overlapping remits**: `orchestration/router.py:29` (live, task),
  `capabilities/router.py:40` (dead), `platforms/knowledge_router.py:44` (live, CLI-only),
  `intelligence/selection/router.py:15` (live, models). Each docstring claims to be the *other* one.
- **Two health trackers with opposite optimism**: `ToolHealthTracker` (EWMA α=0.3, unknown →
  **0.5**, "not optimistic") vs `CapabilityHealth` (deque 50 + CircuitBreaker, unknown → **1.0**).
- **Two `Contact` models**: `domain/contacts.py:35` and `domain/email.py:74`.
- **Two catalog renderers**: `ToolRegistry.catalog()` (used, wrong data) and
  `ToolRouter.catalog()` (correct-ish, unused).
- **`name = "filesystem"` on two classes**: `FilesystemTool` and `EchoTool` (`cli.py:32`,
  deliberately, so manifest rules apply).

## Other defects

- `SafetyEngine.guard` uses `req.correlation_id` as `task_id` for every safety event
  (`engine.py:128`, comment "Using correlation_id as task_id proxy") → **safety events are not
  joinable to tasks.**
- `_confirm` returns `False` when no confirmer is set (`engine.py:247-249`) — correct fail-closed
  — but `bootstrap/safety.py:39-46` constructs `SafetyEngine` **without** a confirmer and
  `app.py:458` sets it later. Anything guarded in between silently denies.
- `whatsapp.known_contacts` (`permissions.yaml:11-12`) and the `contact_known` constraint
  (`classifier.py:190`) exist for a tool that doesn't exist — and **`Manifest.whatsapp` is a
  required field** (`manifest.py:44`), so removing the stanza breaks manifest loading.
- `capabilities/providers/mcp/base.py` exists (3.6 KB) with an empty `__init__.py`;
  `providers/base.py:6-8` sells MCP as the scaling story, but **no MCP provider is registered**.
- Dead methods: `ToolRegistry.tool_call_specs`, `ToolRouter.rank`/`catalog`,
  `CapabilityRegistry.registered_tools`, `CapabilityHealth.snapshot`,
  `ProviderRegistry.all_providers`, `CapabilitySpec.version`/`dependencies`/`default_tier`,
  `ToolRequest.declared_tier_hint`, `ToolMetadata.safety_tool` (never populated in
  `_metadata_map`, `bootstrap/orchestration.py:88-104`).
- Stale phase claims guarding live code: `tools/base.py:2-3` "real tools arrive in Phase 2";
  `interfaces/cli.py:2-3` and `diagnostics/doctor.py:29-30` "Phase 1 has no real tools".
