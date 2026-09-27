# ATLAS Runtime Flow

> End-to-end execution of a task, from an inbound request to a persisted, learned outcome.

## Happy Path (single task)

```
InboundEvent (API POST /tasks · CLI run · scheduler)
   │
   ▼
Orchestrator.run()
   1. Task created (immutable Pydantic model) + INSERT into tasks table
   2. task.created event → MessageBus (durable dual-write)
   3. State machine: CREATED → READY → BUILDING_CONTEXT
   4. Router.route() ─ LLM-lite classification → Capabilities
      (needs_memory/retrieval/tools/reasoning/confirmation/cloud, max_risk)
   5. ContextBuilder.build() ─ hybrid retrieval:
        5 parallel queries (semantic facts, keyword episodes, vector episodes,
        user model, knowledge store) → RRF fusion → salience boost →
        token-budget knapsack (~1500 tokens)
   6. PLANNING: Planner.plan() ─ LLM JSON plan → Plan{goal, steps, risk,
      confidence, constraints, termination_conditions}
   7. GoalState built from plan (objective, constraints, success criteria,
      replan budget = 3)
   8. planning.finished event
   9. ReasoningLoop.run() ── the OTAR loop:
        while not terminal:
          a. limits.tick_step()  (max 15 steps, tokens, runtime)
          b. REASONING: model call (capability-routed, health-selected,
             budget-governed, cached) → ResponseParser → Thought + Action
          c. switch Action.kind:
             • final_answer / ask_user:
                 VALIDATING → Verifier.verify(goal, answer)
                 - pass → COMPLETED → TaskResult
                 - fail + replans left → Replanner.replan() → loop
             • tool_call:
                 SelfCritique.critique() (revise/abort/ok)
                 WAITING_TOOL → EXECUTING (tool.requested event)
                 SafetyEngine.guard():
                   kill-switch → classify (deny-by-default manifest,
                     hard-block matchers, constraints) → policy → audit →
                     [confirm: prompt/dry-run preview, code for DANGEROUS] →
                     re-check kill-switch → execute (sandboxed) → audit result
                 RetryManager wraps dispatch (≤3 retries, recoverable only)
                 OBSERVING → observation into WorkingMemory + trajectory
                 - failure + replans left → Replanner.replan() → loop
                 Reflection.reflect() → learnings logged
          d. history.append(thought, obs)
  10. Trajectory saved (single transaction, <50ms target)
  11. Experience extraction launched async (LLM, 0–3 lessons,
      confidence ≥ 0.5, does not block result)
  12. task.completed/task.failed event; tasks row updated in finally block
```

## State Machine (legal transitions only)

`created → ready → building_context → planning → reasoning ⇄ {waiting_tool → executing → observing, waiting_confirmation, validating, retrying} → completed | failed | cancelling → failed → archived`

Any illegal transition raises `IllegalTransitionError` immediately.

## Failure Paths

| Failure | Behavior |
|---|---|
| Model call invalid/timeout | `with_timeout` + provider fallback chain; parse failure → typed `ReasoningError` → graceful FAILED |
| Tool failure | Retry (recoverable only, ≤3) → replan (≤3) → FAILED with error in TaskResult |
| Safety denial | `DeniedError` propagates; every decision audited before raise |
| Kill switch active | `HaltedError`; re-checked after any confirmation wait |
| Limit exceeded | Typed error from `LimitCounter` → graceful FAILED, never a crash |
| Cancellation | `CancellationToken` → CANCELLING → FAILED with reason |
| Trajectory save failure | Logged, task result unaffected |
| Experience extraction failure | Logged (async fire-and-forget), no impact |
| Classifier internal error | Fail-closed → `require_confirm` at error tier |
| Bus batch failure | At-least-once dispatch; handler exceptions isolated via gather |

## Event Stream (observability)

Per task, sequenced events land in `task_events` and fan out to the MessageBus:
`task.created, task.started, context.building, planning.started/finished,
reasoning.thought/action/step, tool.requested/executing/completed/failed,
replan.started/finished, tier.classified, approval.requested/resolved/denied,
memory.retrieved, task.completed/failed`.

API consumers choose SSE (`/tasks/{id}/events/stream`, `Last-Event-ID` resume)
or WebSocket (global firehose or task-scoped with DB replay).

## Universal Tooling Path (foundation)

Parallel to the loop-internal dispatch above, every tool in the system is also
reachable through one universal facade (`Atlas.tooling`, built in
`bootstrap/tooling.py`):

```
UniversalToolInvocation → ToolingExecutor → adapter
    → ToolDispatcher (native) | CapabilityDispatcher (capability)
    → SafetyEngine.guard() → backend → UniversalToolResult
```

The fabric adds no second funnel: adapters call the same dispatchers the
ReasoningLoop uses, so tier classification, policy, audit and confirmation are
identical. Structured logs (`tooling.execution.started/completed/failed`,
correlation + task ids) ride the standard structlog pipeline; registry state is
`registered/ready/disabled/failed` with initialize-time failure isolation. See
`docs/tooling/foundation.md`.

At startup (`Atlas.start()`, after the database and bus are live) the live
registry is reconciled into the persistent **Tool Catalog**: per-source
discovery → fingerprint diff → one SQLite transaction per source →
`tool.catalog.*` events → atomic in-memory index swap. Failed source refreshes
retain previous rows; absent-after-success becomes STALE, never deleted.
Lexical/structured search, `find_candidates()` (the Part-3 router seam), and
`atlas tools catalog|search|inspect|namespaces|refresh` all read the index.
See `docs/tooling/catalog.md`.

## Background Loops

- MessageBus queue processor (batches of 50)
- Embedding worker (async Chroma indexing)
- CronScheduler — memory consolidation daily 02:00
- Notification queue with quiet hours, rate limits, retry

## Learning Loop (post-task)

```
Trajectory (actions, observations, replans, verification, cost, latency)
   → ExperienceExtractor (LLM, async)
   → Experiences (category, lesson, applicability, confidence)
   → future: skill promotion at reuse threshold, experience-informed planning
```

## Routing Fabric (Part 3)

`atlas.routing` (built in `bootstrap/routing.py`) is the control plane over the tooling
fabric: it normalizes any ingress into a `TaskIR`, selects domain (deterministic
fast-path rules → judgment cascade) and strategy, decomposes capabilities against real
registrations, pulls candidates from the Part-2 catalog, applies the hard policy filter
BEFORE any judgment, ranks deterministically, and compiles a validated `RoutePlan` +
`RouteGraph`. Decisions are persisted (`route_decisions`) and replayable; failures are
classified into routing `FailureCategory` values and walk a bounded recovery ladder.
Routing never executes — plans flow through the existing governed funnels. Events on the
`route` bus topic; see `docs/routing/architecture.md`.
## Durable Execution Fabric (Part 4)

`atlas.execution_engine` executes Part-3 RoutePlans reliably: plan validation
before anything runs, dependency-aware scheduling with explicit join policies,
bounded concurrency slots, per-step staleness revalidation against the live
catalog, and ONE governed execution boundary — every tool step flows through
`ToolingExecutor → SafetyEngine.guard()`. Failures are classified and walked
through a bounded retry/fallback/replan/human ladder by a recovery controller
that decides but never executes. State is durable: SQLite runs + versioned
checkpoints at step boundaries, resume after crash/pause/human-wait without
re-running completed steps, at-least-once external semantics with
side-effect-aware retry (non-idempotent sends are never auto-retried).
Events on the `execution` topic with per-run sequence numbers. See
`docs/execution/architecture.md` and the Temporal ADR therein.
## MCP Runtime (Part 5)

`atlas.mcp_manager` connects owner-configured MCP servers through the official
SDK (`mcp>=2.2.0`) behind an ATLAS boundary: stdio (structured argv, allowlisted
env) and Streamable HTTP (SSRF-guarded endpoints) transports; paginated
`tools/list` discovery normalized into `UniversalToolDefinition`s with stable
ids `mcp:<server>:<tool>`; dynamic tool-list changes → debounced re-discovery →
ToolingRegistry + ToolCatalog sync WITHOUT restart. MCP tools route and execute
through the ordinary Part-3/4 paths — the SafetyEngine gate is never bypassed.
Connection state machine, bounded reconnect, and per-server health counters
are explicit; secrets live only in the credential vault as references. See
`docs/tooling/mcp/architecture.md`.