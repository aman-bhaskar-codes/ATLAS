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
