# ATLAS Dependency Graph

> Layer direction is machine-enforced by `importlinter.ini`. Higher layers import lower ones, never reverse.

## Enforced Layer Hierarchy (import-linter contract 1)

```
interfaces ──▶ diagnostics ──▶ orchestration ──▶ capabilities ──▶ memory
                                    │                             │
                                    ▼                             ▼
                              intelligence ──▶ safety ──▶ tools ──▶ perception
                                    │             │         │
                                    ▼             │         ▼
                                  control ◀───────┴──── infra
```

Exact declared order (top may import down):
`interfaces > diagnostics > adaptation > evaluation > tooling > orchestration > knowledge > capabilities > memory > intelligence > safety > tools > perception > control > infra`

`atlas.tooling` (universal tooling fabric) sits ABOVE `orchestration` because it
wraps the dispatchers — see `docs/tooling/foundation.md`.

Three whitelisted exceptions exist (documented in `importlinter.ini`).

## Additional Forbidden Contracts

- **Contract 2:** `atlas.infra` may NOT import safety/tools/interfaces/diagnostics/capabilities/intelligence/memory — infrastructure knows no policy.
- **Contract 3:** `atlas.safety` and `atlas.tools` may NOT import provider SDKs (`atlas.infra.providers`).

## Composition Root Wiring (app.py + bootstrap/)

```
build()
 ├── bootstrap.infrastructure ─▶ Settings, AppConfig, Database, BackendRouter
 │                               (domain→Connection seam, registered "backend_router"
 │                               deps=("db",)), MessageBus, AuditLog,
 │                               KillSwitch, IdGenerator, Clock, Metrics, Tracer
 ├── bootstrap.safety ─────────▶ TierClassifier, SafetyEngine (manifest-driven)
 ├── IdentityPlatform           (secret store on Database; outbound credentials only)
 ├── bootstrap.intelligence ───▶ ProviderRegistry (Ollama + optional cloud),
 │                               ModelRegistry (models.yaml), HealthMonitor,
 │                               CostGovernor + Budgets, SemanticCache,
 │                               InferenceRuntime, FallbackEngine, ModelGateway,
 │                               OllamaEmbedder, LLMCallTracker
 ├── NotificationPlatform ─────▶ confirmer wired into SafetyEngine
 ├── CapabilityRegistry/Dispatcher + SafetyEngine
 ├── Sandboxes (Docker; native in dev)
 ├── tools: filesystem, shell ─▶ ToolRegistry
 ├── bootstrap.memory ─────────▶ ChromaVectorStore, Episodic/Semantic/UserModel/
 │                               Working/KnowledgeStore, Retriever, Consolidator,
 │                               Pruner, TrajectoryStore, ExperienceExtractor
 ├── Platforms: Knowledge, Email, Calendar, Contacts, Browser(optional)
 ├── bootstrap.orchestration ──▶ Router, Planner, ContextBuilder, ResponseParser,
 │                               OutputValidator, PromptBuilder, ExecutionRecorder,
 │                               ExecutionMonitor, RetryManager, SelfCritique,
 │                               ToolDispatcher, Replanner, Verifier,
 │                               ReasoningLoop, Orchestrator
 ├── bootstrap.tooling ────────▶ ToolingFabric (ToolingRegistry + Native/Capability
 │                               adapters + ToolingExecutor; SafetyEngine unchanged)
 │                               + ToolCatalog (SQLite: sources→namespaces→tools→operations,
 │                               fingerprints, sync engine, in-memory search index)
 ├── bootstrap.routing ────────▶ RoutingEngine (TaskIR→domain→strategy→capabilities→
 │                               candidates→hard filter→judgment→ranking→plan/graph→recovery;
 │                               DomainRegistry, StrategyRegistry, judgment cascade, RouteStore)
 ├── bootstrap.execution ──────▶ ExecutionEngine (scheduler, slots, adapters, retry,
 │                               recovery controller, SQLite checkpoints/resume; tool steps
 │                               flow through ToolingExecutor → SafetyEngine)
 ├── bootstrap.mcp ────────────▶ MCPServerManager (official SDK behind an ATLAS boundary:
 │                               stdio/HTTP transports, paginated discovery, normalization
 │                               to UniversalToolDefinition, dynamic catalog sync, bounded
 │                               reconnect, security policies)
 └── FeedbackStore, CronScheduler (2 AM consolidation), WorkflowStore
```

## Key Runtime Dependency Paths

**Task execution:**
`Orchestrator → Router → ContextBuilder → Retriever (memory) → Planner → ModelGateway → ReasoningLoop → ToolDispatcher → SafetyEngine → Tools`

**Model inference:**
`caller → ModelGateway.complete()/infer() → CapabilityRouter → ModelSelector → FallbackEngine → InferenceRuntime → Provider (Ollama/cloud)`

**Memory read:**
`ContextBuilder → Retriever → {SemanticMemory, EpisodicMemory, UserModel, KnowledgeStore} (parallel) → RRF fusion`

**Event fan-out:**
`EventPublisher → MessageBus → event_queue/event_log (SQLite) → handlers + API SSE/WS broadcasters`

**Persistence resolution (Storage Backbone):**
`store → BackendRouter.resolve_backend(domain) → {PostgresConnection (deduped per DSN, lazy asyncpg pool) | shared SQLite Database} — fallback chain: domain DSN → legacy alias → CORE DSN → SQLite`

**Schema provisioning (Atlas.start):**
`Atlas.start() → lifecycle.start() → SchemaProvisioner.provision() → per distinct Postgres DSN: translate _MIGRATIONS → apply with per-account schema_version (CORE unreachable=fatal, optional=warn+degrade)`

**Capability call:**
`Action → ToolDispatcher → CapabilityDispatcher → SafetyEngine.guard() → platform provider → audit`

**Universal tooling (foundation):**
`UniversalToolInvocation → ToolingExecutor → {NativeToolAdapter → ToolDispatcher | CapabilityAdapter → CapabilityDispatcher} → SafetyEngine.guard() → backend → UniversalToolResult`

**Tool catalog sync (Part 2):**
`Atlas.start() → ToolingFabric.sync_catalog() → CatalogSource.discover() → validate+diff(fingerprints) → SQLite transaction (tool_definitions/tool_operations + catalog_version++) → tool.catalog events → in-memory index swap → find/search/inspect`

**Routing decision (Part 3):**
`request → TaskIR → domain (rules→judgment cascade) → strategy → capabilities → catalog candidates → hard policy filter → bounded judgment → ranking → RoutePlan/RouteGraph (validated, persisted, replayable) → recovery routing on failure`

**Plan execution (Part 4):**
`RoutePlan → ExecutionEngine (validate → StepScheduler → slots → staleness revalidation) → ToolingExecutor → SafetyEngine.guard() → backend → ExecutionObservation → retry/fallback/replan/human (bounded RecoveryController, SQLite checkpoints) → ExecutionRunResult`

**MCP runtime (Part 5):**
`config/mcp.yaml → MCPServerManager → official SDK (stdio/HTTP) → tools/list (paginated) → normalization → ToolingRegistry + ToolCatalog sync (no restart) → tools/call through the governed funnel → UniversalToolResult with MCP provenance`

## Known Coupling Debt

1. `Orchestrator` depends directly on `Database` (raw SQL) rather than a store abstraction — being fixed in Batch 1.
2. `ModelGateway.health()` reaches into `runtime._providers`/`runtime._health` privates.
3. `Retriever.set_events()` / `SafetyEngine.set_events()` use `Any` to dodge a circular import.
4. `bootstrap/orchestration.py` and some store classes take `Any`-typed parameters for bus/stores.
5. `app.py` still constructs capability platforms inline (~200 lines) — a `bootstrap/capabilities.py` builder is the planned extraction.
