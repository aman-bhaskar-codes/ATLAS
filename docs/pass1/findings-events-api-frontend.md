# Phase 0 Findings — Events, API, Frontend Contract

## Three event shapes on one path (Phase 22 conflict)

1. **`Event`** (`infra/bus.py:28-32`) — in-process base. **One field: `correlation_id`.** All
   payload lives in subclasses.
2. **`AtlasEvent`** (`autonomy/events.py:32-50`) — the canonical persisted envelope: `id, type,
   source, correlation_id, causation_id, deduplication_key, occurred_at, payload, metadata,
   schema_version`.
3. **`TaskEventResponse`** (`interfaces/api/schemas.py:67-85`) — the SSE wire shape, a **third,
   different** shape: `schema_version, event_id, event_type, ts, task_id, correlation_id,
   execution_id, sequence, state, summary, capability, operation, provider, tier: int|None,
   requires_approval, safe_metadata`.

Subclasses (`orchestration/events.py`): `OrchestratorEvent` :17, `SafetyEvent` :25,
`PlanningEvent` :40, `MemoryEvent` :51, `ToolEvent` :63, `FeedbackEvent` :77, plus
`MemoryBusEvent` (`bus.py:41`).

**Latent bug in all 7 subclasses:** they are pydantic `BaseModel`s but use
`dataclasses.field(default_factory=dict)` for `metadata` (`orchestration/events.py:11`).
Pydantic does not interpret `dataclasses.field`, so the default is a `Field` *object*, not `{}`.
Masked only because every emitter passes `metadata=` explicitly.

**Serialization** (`bus.py:104-144`): `type` = **the topic**, not the `kind`. `causation_id` is
overloaded to carry `task_id`. `AtlasEvent.payload` is built and then **discarded** — the column
stores `event.model_dump_json()` (the subclass) instead.

**Type contradiction:** `SafetyEvent.tier` is `str` (`engine.py:131` `tier=decision.tier.name`)
but `TaskEventResponse.tier` is `int|None` and the frontend Zod is `z.number().int().nullable()`
(`contracts.ts:72`). Moot anyway — tier never survives the trip.

## Phase 22 target list vs reality

Emitted today (`orchestrator` topic, 15 distinct kinds): `task.created` :116, `task.started`
:121, `context.building` :125, `planning.started` :138, `planning.finished` :152,
`task.completed`/`task.failed` :206, `reasoning.thought` (`reasoning.py:163`),
`reasoning.action` :173, `reasoning.step` :484, `replan.started` :203/:332, `replan.finished`
:229/:357, `tool.requested` :269, `tool.executing` :280, `tool.completed`/`tool.failed` :292.

`safety` topic: `tier.classified` (`engine.py:130`), `approval.requested` :161,
`approval.resolved` :191, `approval.denied` :144/:176.
`memory`: `memory.stored`, `memory.retrieved`, `memory.user_model_updated`, `trajectory.saved`.
`provider.*`: `quota_exhausted`, `selected`, `rate_limited`, `failed`, `fallback`.

| Target | Status |
|---|---|
| task.created / task.started | ✅ |
| **intent.created** | ❌ MISSING — 0 hits |
| **capabilities.selected** | ❌ MISSING — `router.route()` at `orchestrator.py:127` emits nothing |
| planning.started / finished | ✅ |
| **reasoning.started** | ❌ MISSING |
| reasoning.step | ✅ |
| tool.requested | ✅ |
| **approval.required** | ⚠️ RENAMED → `approval.requested`, and on the `safety` topic which has **no `task_events` writer** |
| **tool.started** | ⚠️ RENAMED → `tool.executing` |
| tool.completed | ✅ |
| **verification.started** | ❌ MISSING — `verify()` at `reasoning.py:195` emits nothing |
| **verification.completed** | ❌ MISSING — result reaches `TaskResult` only, never the bus |
| replan.started | ✅ |
| **replan.completed** | ⚠️ RENAMED → `replan.finished` |
| task.completed / task.failed | ✅ |

**5 missing outright, 3 renamed.**

Declared-but-never-emitted: the entire `Topic` class (`bus.py:61-71`, 11 constants, zero
publishers). **`emit_planning`, `emit_tool`, `emit_feedback` have ZERO call sites** →
`PlanningEvent`/`ToolEvent`/`FeedbackEvent` are dead types, and `EventBroadcaster` subscribes to
`planning`/`tool` (`websocket.py:144`) — topics nothing publishes to. `UserModelStore` subscribes
to `feedback` (`user_model.py:43`), likewise dead and not even in `register_type`.

## P0 — There is NO dual-write; and handler failures silently lose events

**Correction to the prior architecture belief:** `event_queue` (`db.py:277`) and `event_log`
(`db.py:287`) are **dead schema — nothing in `src/` reads or writes either.** The only writer is
`bus.publish` → single INSERT into `events` (`db.py:588`). `routes_events.py:123` still claims
"replay from event_queue" while the query at :181-190 hits `events`.

`_process_queue` (`bus.py:147-220`): 50 rows/pass, `ORDER BY occurred_at ASC` on a **string**
column with no tiebreak → same-microsecond ordering undefined.

**Poison events are dead-lettered correctly** (deserialization failure → `dead_letter` +
reason). **But a HANDLER exception is logged at WARNING and the event is still appended to
`delivered_ids`** (`bus.py:200` sits outside the try) → **silent, permanent event loss for that
subscriber.** `attempt_count` and `next_retry_at` columns exist and are **never incremented or
read — no retry, ever.**

**At-least-once with no idempotency guard:** the `UPDATE … 'delivered'` commit happens *after*
handlers ran. A crash in between re-reads the whole batch as `pending` and re-runs every handler.
`TaskEventStore.record` (`event_store.py:47-96`) mints a fresh `uuid4()` and computes `sequence`
via `MAX(sequence)+1`, so redelivery **duplicates `task_events` rows with new event_ids and new
sequences** — which the frontend's `reconcile()` dedupes by `event_id` and therefore *cannot*
dedupe, then trips `hasGap` → resync loop.

**Two more races:** `app.py:106` and `:145` register two separate `orchestrator` subscribers
(one writes `task_events`, one wakes SSE) run concurrently by `gather` → SSE can wake before the
row commits. And the SSE generator checks terminal state after draining (`events.py:104-108`)
while the orchestrator commits `tasks.state='completed'` in its `finally`
(`orchestrator.py:212-216`) — so **`stream_closed` can fire before the final event lands.**

**CLI blind spot:** `TaskEventStore` is instantiated only at `app.py:58` inside the FastAPI
lifespan. CLI/test runs write **no** `task_events` at all.

## Registered but UNREACHABLE routes

1. **`GET /api/v1/providers/health` — duplicate path, two owners.** `capabilities_router` is
   included at `app.py:242`, `providers_router` at `:253`; first-match-wins → `routes_capabilities.py:20`
   serves it and **`routes_providers.py:16` is dead code**. The frontend expects
   `ProviderHealth[]` and receives `RuntimeHealthResponse` — hard shape break. (This is also
   why the `health.latency()` `AttributeError` from findings-intelligence.md is latent rather
   than fatal.)
2. **Six trajectory routes shadowed** by `/{trajectory_id}` registered at
   `routes_trajectory.py:212` *before* `/recent` :304, `/failed` :333, `/decisions` :367,
   `/failures` :415, `/experiences` :499, `/stats` :554 — all single-segment, so they resolve to
   `get_trajectory("recent")`. **`tests/api/test_routes_registered.py:38-41` asserts only that
   the paths appear in the OpenAPI spec, so the test passes while the endpoints are broken.**
3. **`WS /api/v1/events` and `WS /api/v1/tasks/{task_id}/events/ws` crash on connect** —
   they read `app.state.global_ws_queues` (`events.py:156`) and `app.state.ws_queues` (:198),
   **neither ever set** (`app.py:147-157` sets only `sse_queues`). Guaranteed `AttributeError`,
   and no producer would fill them anyway.

## Health endpoints — Phase 21 partly exists, UI never reads it

All three exist at **`/api/v1/live|ready|health`** (not bare paths).

- **`/live`** (`health.py:69`) checks **nothing** — unconditional `alive=True`.
- **`/ready`** (`health.py:87`) returns `state`, `degraded_components`,
  `unavailable_capabilities`. **Fails OPEN**: if `runtime_supervisor is None` it returns
  `ready=True, state="READY"` (:98-106) — a hardcoded lie. `DEGRADED` counts as ready.
- **`/health`** (`health.py:120`) → `supervisor.get_health_report()`; the `None` fallback
  (:130-149) checks only `db.health()` and hardcodes `latency_ms=0.0`.

`SystemState` (`bootstrap/runtime.py:31-40`) has all 8 states ATLAS needs:
`BOOTING, INITIALIZING, DEGRADED, READY, BUSY, RECOVERING, SHUTTING_DOWN, FAILED`.

**The frontend consumes NONE of it.** Grepping `frontend/` for `/ready`, `/live`,
`/api/v1/health`, `BOOTING`, `DEGRADED`, `degraded_components`, `unavailable_capabilities`
returns **zero** hits. `client.ts` has no method for any of the three.

What the UI reads is `/api/v1/runtime/status`, whose `state` is a **two-valued kill-switch
derivation, not the supervisor** — `facade.py:80`: `state="ready" if not kill_switch else
"degraded"`. It also hardcodes `version="1.0.0"` (:81, ignoring `app.state.version` set at
`app.py:154`) and `pending_approval_count=0` (:85). So `RuntimeHealthPanel.tsx:69` renders a
permanent `0` and :57 a permanent `v1.0.0`.

**Net: BOOTING/INITIALIZING/BUSY/RECOVERING/SHUTTING_DOWN/FAILED never reach the UI.**

## P0 — Approval flow is severed at BOTH ends

Backend path (`safety/engine.py:118-197`): classify → `emit_safety(tier.classified)` →
if `require_confirm` → `emit_safety(approval.requested)` → **blocking
`await self._confirm(...)`** :169 → `approval.resolved` or `approval.denied` → `_execute` or
`raise DeniedError`.

**No HTTP hop, no persistence.** There is **no `approvals` table**, no approval id is ever
minted (`SafetyEvent.approval_id` is `None` at all 5 emit sites), and pending state exists
**only as a suspended coroutine frame inside `guard`**.

Outbound (pending → UI) broken at 3 points: (a) `approval.requested` goes to topic `safety`;
`TaskEventStore` subscribes only to `orchestrator` → never reaches SSE. (b)
`GET /approvals/pending` returns a hardcoded `[]` (`facade.py:220-223`). (c)
`GET /approvals/{id}` unconditionally `raise KeyError` (`trust_facade.py:60-61`).

Inbound (decision → backend) broken: `POST /approvals/{id}/decide` →
`raise NotImplementedError` (`facade.py:226`) → **500 on every click**. And even if implemented,
**there is no channel to the waiting coroutine** — the two halves are architecturally
unconnected. Two divergent request bodies also exist (`client.ts:92` vs `:119`).

**`requires_approval` is structurally always `false`**: `TaskEventStore.record` is invoked only
from `app.py:69-104` (orchestrator topic), reading `event.metadata.get("requires_approval")`,
but that flag is only ever set on `SafetyEvent` on the `safety` topic. So
`pendingApprovalEvent()` (`contracts.ts:165`) is dead code.

**In API-server mode the confirm step hangs the process.** `CompositeConfirmer`
(`app.py:458`) falls back to `CliConfirmer`, which does
`await asyncio.to_thread(input, "approve? [y/N] ")` (`notify.py:80-82`) — no TTY under uvicorn
⇒ blocks forever or `EOFError`, task stuck in `WAITING_TOOL`.

Also inert: `ApprovalInbox.tsx:41-46` buttons have **no `onClick`**;
`app/approvals/page.tsx:46-68` "Recently Resolved" is **hardcoded fake data**.

## Frontend — three parallel, inconsistent event layers

- **Layer A (SSE, `TaskEventResponse`)** `contracts.ts:57-75` — the only one wired to a real
  page (`app/tasks/[task_id]/page.tsx:21`). `event_type: z.string()` — **never validated**,
  which is why the renames below fail silently.
- **Layer B (WebSocket, raw subclass)** `lib/websocket/index.ts:11-20` — field is **`kind`**,
  not `event_type`. Incompatible with A. Used by `dashboard/page.tsx:62` and
  `page-enhanced.tsx:126`.
- **Layer C (memory WS)** `features/memory/contracts.ts:91-99` — its own 7-value enum.

Concrete mismatches:

| Frontend expects | Backend emits | Location |
|---|---|---|
| `event_type === "completed"` | `task.completed` | `runtime/ActivityTimeline.tsx:22` — success icon **never renders** |
| `event_type === "failed"` | `task.failed` | `runtime/ActivityTimeline.tsx:25` — error icon **never renders** |
| `'safety_gate'` | **nothing** — 0 hits in `src/` | `LiveRunPage.tsx:55` — invented name |
| `memory.consolidated`, `memory.pruned`, `memory.fact_added`, `memory.knowledge_indexed` | never emitted | `features/memory/contracts.ts:94-98` |
| — | `trajectory.saved` **is** emitted but absent from the frontend enum | orphaned |
| `scheduler.tick`, `memory.consolidate_requested`, `webhook.received` | never published (backend uses `webhook.{source}`) | `features/autonomy/templates.ts:18,44,57` — **these automations can never fire** |
| `metadata.action` | `reasoning.action` sets **`action_kind`** | `EventCards.tsx:15` — action panel never renders |
| `metadata.result` / `.results` | `emit_tool` never called; `emit_memory` sets `items` | `EventCards.tsx:53,169` |

Only `state` mapping works (`TASK_STATES` matches `TaskState.value`) — which is why the broken
`event_type` comparisons went unnoticed.

**`NEXT_PUBLIC_ATLAS_API_URL` has two incompatible contracts.** `client.ts:9`,
`useTaskEvents.ts:8`, `socket.ts:9` treat it as **including** `/api/v1`;
`dashboard/page.tsx:23`, `page-list.tsx:20`, `events/search/page.tsx:30`,
`page-enhanced.tsx:25` treat it as a **bare origin** and append `/api/v1/...`. **Setting it
correctly for one set breaks the other**, and both default ports (8000, and 8730) disagree —
the server listens on 8730. `lib/websocket/index.ts:46,77,146` default to `ws://localhost:8000`.

Other frontend breaks: `trajectoryApi.experiences` (`client.ts:248`) produces
`/api/v1/api/v1/trajectory/experiences` → 404; `atlasApi.tasks()` parses `TaskPage` output with
`TaskSchema`; **three incompatible connection-state vocabularies** (`contracts.ts:155` vs
`EventConnectionBadge.tsx:6` vs `useWebSocket.ts:14`); `lib/events/socket.ts:19` uses
`source.onmessage`, which never fires for **named** SSE events (server always sends
`event: task_event`); `LiveRunPage.tsx:140` hardcodes `Tokens: ~4200 · Cost: $0.018`; four sets
of duplicate divergent components.

## Security-relevant

- **`POST /api/v1/events/emit`** (`routes_events.py:341`) lets any caller inject **any topic**
  with `extra="allow"`, **unauthenticated by default** (`app.py:196-198`: no keys ⇒
  `api_keys={}` ⇒ open) — and the **TriggerEngine (`app.py:65`) acts on it.** Arbitrary event
  injection → automation execution. Must be gated in Phase 31/36.
- CORS hardcoded to `http://localhost:3000` with `allow_methods=["GET","POST"]`
  (`app.py:177-184`) → `PUT`/`DELETE /api/v1/automations/{id}` are **blocked from the browser**
  though `client.ts:165,171` call them.
- `/api/v1/ready` fails open when the supervisor is `None`.
- `MemoryBroadcaster._broadcast_handler` reaches into `self._manager._lock`/`._active`
  privates (`websocket.py:202-203`).

## Unrouted protocol methods

`AtlasTrustPlane` declares 6 methods with **no route**: `pending_approvals`, `decide_approval`,
`memory_provenance`, `supersede_memory`, `delete_memory`, `audit_event`
(`control_plane.py:25-49`). Every `routes_*.py` router *is* included in `create_app()` — nothing
orphaned at module level.
