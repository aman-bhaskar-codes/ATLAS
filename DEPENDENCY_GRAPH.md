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
`interfaces > diagnostics > orchestration > capabilities > memory > intelligence > safety > tools > perception > control > infra`

Three whitelisted exceptions exist (documented in `importlinter.ini`).

## Additional Forbidden Contracts

- **Contract 2:** `atlas.infra` may NOT import safety/tools/interfaces/diagnostics/capabilities/intelligence/memory — infrastructure knows no policy.
- **Contract 3:** `atlas.safety` and `atlas.tools` may NOT import provider SDKs (`atlas.infra.providers`).

## Composition Root Wiring (app.py + bootstrap/)

```
build()
 ├── bootstrap.infrastructure ─▶ Settings, AppConfig, Database, MessageBus, AuditLog,
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

**Capability call:**
`Action → ToolDispatcher → CapabilityDispatcher → SafetyEngine.guard() → platform provider → audit`

## Known Coupling Debt

1. `Orchestrator` depends directly on `Database` (raw SQL) rather than a store abstraction — being fixed in Batch 1.
2. `ModelGateway.health()` reaches into `runtime._providers`/`runtime._health` privates.
3. `Retriever.set_events()` / `SafetyEngine.set_events()` use `Any` to dodge a circular import.
4. `bootstrap/orchestration.py` and some store classes take `Any`-typed parameters for bus/stores.
5. `app.py` still constructs capability platforms inline (~200 lines) — a `bootstrap/capabilities.py` builder is the planned extraction.
