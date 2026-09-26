# Execution Fabric Architecture (Part 4)

> Describes **implemented** behavior. Deferred items are marked.

## ADR: why ATLAS does NOT embed Temporal (or any external orchestrator)

* ATLAS already owns task persistence (`execution_runs`/`execution_run_checkpoints`
  on its WAL SQLite), a durable event bus, checkpoints, and cooperative
  cancellation — a second control plane would duplicate them.
* The requirement is an EMBEDDED execution fabric on a local Mac / Docker /
  free-tier box: no Temporal server, Kafka, Redis, or Kubernetes (§90).
* Temporal-inspired SEMANTICS are adopted (step boundaries, durable state,
  retry policies, event history, capacity slots) without the external system.
* No Temporal-equivalent durability guarantees are claimed: external side
  effects are AT-LEAST-ONCE; idempotency + side-effect-uncertainty handling
  exist precisely because exactly-once is impossible to claim (§22/§82).
* A future distributed worker adapter can replace the in-process scheduler
  behind the engine's interfaces (§89): orchestration / scheduling / step
  execution / state persistence are already separate modules.

## Runtime graph (actual)

```
RoutePlan → ExecutionEngine
  → validate (schema, candidates, deps, cycles, terminal)   §98
  → StepScheduler (explicit depends_on; join policies §41)  §10
  → ExecutionSlots (global + per-candidate semaphores)      §12
  → StepExecutor boundary:
       staleness revalidation (§30-§31)
       → ToolingExecutor → ToolDispatcher/CapabilityDispatcher
       → SafetyEngine → backend                            §13-§15/§21
       → ExecutionObservation (bounded inline data, §54)
  → result validation → retry (§26) / fallback (§29-§30) /
    replan (hook → Part-3 router) / human / terminate       §63
  → checkpoints at every boundary (§19) → resume (§21/§68)
```

## Modules (`src/atlas/tooling/execution/`)

| Module | Responsibility |
|---|---|
| `models.py` | `ExecutionRun`/`StepRun` state machines with legal-transition tables; `TerminalOutcome`; `RetryClass`/`RetryPolicy`; `JoinPolicy`; `ExecutionObservation`; `ExecutionRunResult` |
| `scheduler.py` | dependency-aware ready-set computation; join semantics; dead-end detection (§61) |
| `slots.py` | execution capacity (no unbounded gather, §11-§12) |
| `adapters.py` | tool (governed funnel), agent (injected runtime), workflow (bounded-depth re-entry) |
| `retry.py` | classification, side-effect-aware eligibility, exponential backoff + jitter |
| `recovery.py` | `RecoveryController` (decides, never executes) + `RouteStalenessChecker` |
| `store.py` | SQLite run state + versioned checkpoints |
| `events.py` | immutable `execution.*` events with per-run sequence numbers |
| `engine.py` | the drive loop; start/pause/resume/cancel/status/retry (§99) |

Wiring: `bootstrap/execution.py` → `Atlas.execution_engine`. The Orchestrator
remains the task lifecycle owner; the engine is the route-execution owner (§95).
