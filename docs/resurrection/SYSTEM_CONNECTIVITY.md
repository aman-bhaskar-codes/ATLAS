# SYSTEM CONNECTIVITY MAP

## CANONICAL RUNTIME PATH

The absolute single path every task must travel through the ATLAS architecture:

1. **Intake**: CLI/API/Web -> `Orchestrator.run(event)`
2. **Persistence**: `ExecutionStore.create_task()` (Task State set to `READY`)
3. **Understanding**: `IntentExtractor.understand()` -> extracts intent, sets capability demands.
4. **Context Building**: `ContextBuilder.build()` -> retrieves memory, facts, relevant skills.
5. **Planning**: `Planner.plan()` -> establishes steps, goals, and constraints.
6. **Execution (Reasoning Loop)**: 
   - `ReasoningLoop.run()`: OTAR cycle (Observe -> Think -> Act -> Reflect).
   - `ModelGateway.route()` calls the configured LLM provider.
   - Output parsed by `ResponseParser`.
   - Tool selected and dispatched to `ToolDispatcher`.
7. **Safety & Tools**: 
   - `SafetyEngine.guard()` -> confirms/denies action.
   - Execution via Sandbox/Native environment.
   - Output recorded as `Observation`.
8. **Verification**: `Verifier.verify()` checks final criteria.
9. **Persistence & Telemetry**:
   - `TrajectoryStore.save_trajectory()` captures entire run.
   - Async `ExperienceExtractor` captures semantic lessons.
10. **Events**: `EventPublisher` streams `task.completed` to SSE via `MessageBus`.

---

## SUBSYSTEM MATRIX

| Subsystem | Owner / Class | Entry Point | Primary Dependency | Output / State |
|-----------|--------------|-------------|-------------------|---------------|
| **API Intake** | `app.py` / `facade.py` | `POST /tasks` | `Orchestrator` | Returns `TaskResponse` |
| **Orchestration** | `Orchestrator` | `.run(event)` | `ReasoningLoop`, `Planner` | Triggers Event Bus, DB Task State |
| **Event Bus** | `MessageBus` | `.publish(event)` | `asyncio.Queue` / DLQ | SSE to Frontend, Audit Logs |
| **Reasoning** | `ReasoningLoop` | `.run(plan, context)` | `ModelGateway`, `ToolDispatcher` | `TaskResult` (actions/observations) |
| **Tool Dispatch** | `ToolDispatcher` | `.dispatch(action)`| `SafetyEngine`, `CapabilityRegistry` | `Observation` (tool output) |
| **Safety** | `SafetyEngine` | `.guard(action)` | `Manifest`, `TierClassifier` | Allows/Denies execution |
| **Memory (Working)** | `WorkingMemory` | `.inject(context)` | `ChromaVectorStore` | Expanded prompt string |
| **Memory (Trajectory)**| `TrajectoryStore` | `.save_trajectory()` | `Database` (sqlite) | DB: `trajectories` table |
| **Intelligence** | `ModelGateway` | `.generate()` | `ProviderRegistry` (OpenRouter) | LLM generated string |
| **Database** | `Database` | `.execute()` | SQLite `aiosqlite` | Persistent Rows |
