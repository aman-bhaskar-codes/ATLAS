# ATLAS Scale Path — Modular Monolith → Service Extraction

> Per the hard constraints: NEVER introduce distributed infrastructure
> without measured need. This document maps the million-user LOGICAL
> architecture onto today's modules and defines the trigger that justifies
> each physical extraction. Until a trigger fires, everything stays in one
> process (local mode) or one API + worker (Batch 7 seams).

## Current Deployment Modes

| Mode | What runs | Status |
|---|---|---|
| Local single-user | One process: API + executor + SQLite + Ollama | **Default, fully working** |
| Authenticated LAN/VPS | Same process + `ATLAS_API_KEYS`, rate limits | **Working (Batch 7/8)** |
| Multi-worker | API process + N worker processes on a queue | Seams ready, not needed yet |

## Logical Services → Current Modules

| Logical service | Today's module | Extraction trigger (measured, not speculative) |
|---|---|---|
| API Gateway | `interfaces/api/app.py` + CORS | >1k req/s sustained, or TLS termination needs |
| Auth | `interfaces/api/auth.py` (keys, roles) | Multi-tenant identity (OIDC) — then extract as its own service |
| Tenant resolver | `tenant_id` columns (default 'local') | Second real tenant onboarded |
| Task service | `orchestration/orchestrator.py` + `ExecutionStore` | Task CRUD QPS exceeding single SQLite writer |
| Execution queue | `MessageBus` (durable dual-write SQLite) + `CancellationToken` store | Workers > 1 process (swap SQLite queue for Redis/PG-backed implementation of the same protocol) |
| Workers | In-process asyncio (bounded semaphores everywhere) | CPU saturation on the reasoning loop measured via benchmarks |
| Model router | `intelligence/` (gateway/selector/fallback) | Provider fan-out latency > budget; then extract behind the same `ModelGateway` interface |
| Tool runtime | `orchestration/tool_routing.py` + `dispatcher` | Tool latency p95 dominating task latency; then pool as separate sandboxed workers |
| Memory service | `memory/` (SQLite + Chroma) | Storage > ~10 GB or cross-worker retrieval consistency needed → PostgreSQL + pgvector behind the store protocols |
| Evaluation service | `evaluation/` + `scripts/eval_gate.py` | Continuous (not CI-triggered) evaluation volume |
| Telemetry | `infra/metrics.py`, `tracer.py`, `llm_tracker.py` | Export to OTel collector when a dashboard exists |
| Notification | `capabilities/notification/` | Already async with retries; extract only at high fan-out |

## Seams Already in Place (extraction = new implementation, not rewrite)

- `ExecutionStore` / `CancellationStore` protocols (`orchestration/stores.py`)
- `StorageBackend` protocol for checkpoints (`orchestration/checkpoint.py`)
- `Provider` protocol (`intelligence/providers/base.py`) — vendor isolation
- `Verifier`, `ReflectionHook`, `Evaluator` protocols
- `MessageBus` topic protocol with durable event log + replay
- Rate limiter interface (`interfaces/api/rate_limit.py`) — in-memory today,
  Redis-shaped tomorrow
- Import-linter layer contracts keep every boundary machine-enforced

## When PostgreSQL / Redis Actually Land

1. **SQLite writer contention measured** (busy timeouts under concurrent tasks)
   → implement `PostgresExecutionStore` + port migrations; repositories already
   speak SQL, so the port is mechanical.
2. **Multiple worker processes required** (reasoning CPU saturation) →
   Redis-backed queue implementing the MessageBus protocol; workers become
   stateless consumers; checkpoints already survive restarts.
3. **Vector scale** (Chroma > ~1M chunks or cross-process consistency) →
   pgvector behind `ChromaVectorStore`'s interface.

## Explicitly NOT Now

- Kubernetes, service mesh, microservice split — no measured need.
- Multi-tenant identity (OIDC/LDAP) — single-user product today; tenant_id
  seeds exist so schema work is not blocking later.
- Automatic task resume after crash — requires per-tool idempotency keys;
  fail-clean recovery is the safe default (see `orchestration/recovery.py`).
