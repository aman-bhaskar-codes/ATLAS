# Tooling Fabric — Foundation (Part 1)

> This document describes **implemented** behavior. Nothing here is aspirational;
> what is not built yet is listed under [Deferred](#deferred-to-later-parts).

## Why the fabric exists

ATLAS has several ways to represent "something that can act": native `Tool`
implementations (`atlas.tools`), capability platforms + providers
(`atlas.capabilities`), browser/computer-use subsystems, and knowledge sources.
Each has its own registry vocabulary. That is fine today, but it makes future
tool universes (MCP servers, HTTP APIs, CLI binaries, plugins) a per-source
integration project instead of a configuration problem.

The tooling fabric (`src/atlas/tooling`) adds ONE canonical representation for
every executable capability, plus one registry, plus adapters that bridge the
existing execution funnels. It does **not** replace `atlas.tools`, the
`ToolRegistry`, the `CapabilityDispatcher`, or provider routing — it wraps them.

```
                Reasoning / API / CLI
                        │
                        ▼
             UniversalToolInvocation
                        │
                        ▼
              ToolingFabric (bootstrap-built)
              ├── ToolingRegistry  (identity + metadata + status)
              └── ToolingExecutor  (facade)
                        │
              ┌─────────┴──────────┐
              ▼                    ▼
      NativeToolAdapter     CapabilityAdapter
              │                    │
              ▼                    ▼
      ToolDispatcher      CapabilityDispatcher
              │                    │
              └────────┬───────────┘
                       ▼
                 SafetyEngine      ← the single, unchanged funnel
                       │
                 actual backend
                       │
                       ▼
             UniversalToolResult
```

## Canonical models (`atlas.tooling.models`)

| Model | Purpose |
|---|---|
| `UniversalToolDefinition` | Frozen metadata for one tool: identity, operations, I/O schemas, execution type, provenance, locality, safety seat, policy descriptor, cost/latency priors. Serializable (JSON round-trip is tested). |
| `UniversalToolInvocation` | Frozen request: `tool_id`, `operation`, `arguments`, `correlation_id`, `task_id`, source, timeout, idempotency key. |
| `UniversalToolResult` | Normalized outcome: `ok`, `data`, structured `ToolFailure` (kind + retryable), `duration_ms`, `side_effects`, `provenance`, `metadata`. |
| `ToolPolicyMetadata` | What the tool *declares* (tier hint, side effects, auth, privacy ceiling, network, cost class, trust). Descriptive only — the SafetyEngine is always authoritative. |
| `ToolProvenance` | Thin wrapper over the existing `capabilities.domain.common` `SourceKind`/`Provenance`; converts losslessly. No second provenance universe. |
| `ToolStatus` | Runtime state (`registered` / `ready` / `disabled` / `failed`) — mutable state lives here, never on the definition. |

### Tool identity

Stable, deterministic, collision-resistant: `namespace:provider:tool`
(e.g. `native:atlas:filesystem`, `capability:atlas:knowledge`). Components are
validated (`[a-z0-9][a-z0-9_.-]*`), so IDs are URL/CLI/log safe. Display names
are not identities; `build_tool_id` / `parse_tool_id` are the only
constructors. `definition_version` supports future schema evolution without
breaking old trajectories.

### Input schemas

`ToolInputSchema.from_operations` derives the real operation vocabulary as a
JSON-schema `enum`. Per-operation argument schemas (`operations` map) are an
explicit extensibility point: they stay empty until real schemas exist (MCP
`tools/list` discovery will fill them in a later part) — no invented,
invalid schemas, no bare `{"type": "object"}` everywhere.

## Registry (`atlas.tooling.registry`)

`ToolingRegistry` maps stable ID → (`UniversalToolDefinition`, `ToolAdapter`,
`ToolStatus`). Duplicate registration raises `ToolAlreadyRegistered`;
unknown lookups raise `ToolNotFound`. Structured enumeration
(`list_definitions`, `find`, `find_by_capability/namespace/execution_type`)
is the interface the Part-2 catalog and Part-3 router will build on.
Enable/disable is runtime state: a disabled tool stays inspectable and visible
to future routing but raises `ToolDisabled` on execution.

No global singleton — the registry is constructed in `bootstrap/tooling.py`
and injected (composition-root rule).

## Adapters (`atlas.tooling.adapters`)

The `ToolAdapter` protocol: `initialize / validate / execute / health /
shutdown`. Adapters translate only — no routing, no safety policy, no provider
ranking (§53 of the plan). Two exist:

* **`NativeToolAdapter`** — fronts every tool registered in the orchestration
  `ToolRegistry`. Execution: `UniversalToolInvocation` → `Action` →
  `ToolDispatcher.dispatch` → `SafetyEngine.guard` → `tool.execute` →
  `Observation` → `UniversalToolResult`. Nothing about the native funnel is
  copied or bypassed; denials become `policy_denied` failure results.
* **`CapabilityAdapter`** — fronts every spec in the `CapabilityRegistry`.
  Execution: `UniversalToolInvocation` → `CapabilityRequest` →
  `CapabilityDispatcher.execute` (provider chain, retries, health, telemetry)
  → `SafetyEngine.guard` → provider → `CapabilityResult` →
  `UniversalToolResult` (payload, provenance, provider, latency, cost).
  `CapabilityDenied` becomes a `policy_denied` result at the universal boundary.

Note: side effects do not travel on the `Observation` the native dispatcher
returns — they are preserved in the hash-chained audit log under the
correlation id. The universal result carries normalized data; the audit log
carries the side-effect record.

## Execution (`atlas.tooling.execution`)

`ToolingExecutor` is the one entry point for universal execution:
resolve registration → state checks (`ToolNotFound` / `ToolDisabled` /
`ToolAdapterError` for failed or uninitialized tools) → adapter → governed
funnel → normalized result. Every execution emits structured logs
(`tooling.execution.started/completed/failed` with tool id, operation, adapter,
task id, correlation id, duration) and optional `Metrics` counters — the
existing observability stack, no second telemetry system.

## Fabric lifecycle (`atlas.tooling.fabric`)

`ToolingFabric` owns registry + executor and implements:
`register → initialize → ready → disable → shutdown`. `initialize()` runs
per-tool `initialize` + `validate` with **failure isolation**: a broken adapter
marks its own tool `failed` (with detail) and never blocks unrelated tooling;
the report records ready/failed/skipped explicitly (no fake readiness —
`ready` only after validation passed). `shutdown()` is called from
`Atlas.close()`.

## Bootstrap integration

`bootstrap/tooling.py` (`build_tooling`) is wired in `app.py build()` after
orchestration, and the fabric is exposed as `Atlas.tooling`. It registers
exactly what really exists:

* **Native**: everything in the `ToolRegistry` — `filesystem`, `shell`, plus
  `browser` / `computer_use` / `knowledge` when their platforms are enabled.
  Operations and metadata come from the real registrations; the descriptor tier
  is computed by the real `TierClassifier` against the real manifest (max
  across operations) — documentation, not enforcement.
* **Capabilities**: every registered `CapabilitySpec` — knowledge, weather,
  location, currency, email, contacts, calendar. Capabilities whose providers
  are wired into the `CapabilityDispatcher` (knowledge today) register `ready`;
  the others register with honest status detail ("no provider registered in
  the capability dispatcher") because those platforms execute through their own
  platform classes today.

## Compatibility strategy

* `ReasoningLoop → ToolDispatcher` is unchanged and still works.
* `CapabilityDispatcher` is unchanged and still works.
* `ToolRegistry` / `CapabilityRegistry` / `ProviderRegistry` are untouched.
* `to_tool_call_spec(definition)` (in `atlas.tooling.compat`) converts a
  universal definition into a provider-native `ToolCallSpec` for the later
  model-facing integration.
* The new `atlas.tooling` layer was inserted into the `importlinter.ini`
  layer contract **above `atlas.orchestration`** because it wraps the
  dispatchers; it may import everything below it, nothing above, and nothing
  imports it yet except `interfaces` and `bootstrap`.

## CLI / API

* `GET /api/v1/tools` (`interfaces/api/routes_tools.py`) lists the real
  registry: id, type, capability, operations, status, auth, tier, locality,
  cost class. Key-gated like the other HTTP routers.
* `atlas tools list` (`atlas_cli`) renders the same registry through that
  endpoint. No secrets are ever included (credential *references* only, and
  none exist on Part-1 tools).

## Tests

`tests/tooling/`: model/identity/schema round-trip units, registry state
machine incl. negative cases, both adapters against the **real** SafetyEngine
(allow, deny-by-default denial, disabled tool), fabric isolation, a full
end-to-end test asserting audit records correlated to the original task, a
real-composition-root startup acceptance test, and the CLI render.

## Deferred to later parts

* Part 2: search/filter catalog on top of the registry enumeration.
* Part 3: policy-gated `ToolRoutingEngine` (filter → score → route).
* Part 4–5: MCP client/transports and dynamic discovery (`mcp` execution type
  is representable today; no fake implementation exists).
* Part 6: tool-RAG retrieval. Part 7: fallback/parallel/composition.
* Part 8: per-tool health/circuits/quotas (registry state enum is ready to
  extend). Part 9: full management API + CLI. Part 10: frontend surfaces.
