"""FastAPI application factory.

WHY factory pattern: `create_app()` returns a fresh FastAPI instance each
time. This lets uvicorn use `--factory`, lets tests call `create_app()` with
a clean state, and avoids module-level side effects.

WHY lifespan: startup/shutdown are explicit and symmetric — `Atlas.start()`
is called exactly once before the first request and `Atlas.close()` is called
exactly once after the last. The Atlas object lives on `app.state.atlas` for
the entire process lifetime and is read by every route via `get_atlas()`.

WHY bus subscription here: the SSE event store must receive every event the
orchestrator emits. We wire the subscription in the lifespan so it is set up
exactly once, before the first request, and torn down on shutdown.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from importlib.metadata import version
from typing import TYPE_CHECKING, AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from atlas.app import build
from atlas.infra.bus import Event
from atlas.interfaces.api.errors import atlas_exception_handler

if TYPE_CHECKING:
    from atlas.interfaces.api.event_store import TaskEventStore


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build and start Atlas on startup; close it on shutdown."""
    from atlas.interfaces.api.event_store import TaskEventStore
    from atlas.orchestration.events import OrchestratorEvent

    atlas = await build()
    await atlas.start()

    event_store = TaskEventStore(atlas.db)

    # Subscribe to the orchestrator bus topic so every emitted event is
    # persisted to the task_events table and available for SSE streaming.
    async def _on_orchestrator_event(event: Event) -> None:
        if not isinstance(event, OrchestratorEvent):
            return
        await event_store.record(
            task_id=event.task_id,
            correlation_id=event.correlation_id,
            event_type=event.kind,
            state=event.state,
            summary=event.metadata.get("summary", event.kind),
            capability=event.metadata.get("capability"),
            operation=event.metadata.get("operation"),
            provider=event.metadata.get("provider"),
            tier=event.metadata.get("tier"),
            requires_approval=bool(event.metadata.get("requires_approval", False)),
            safe_metadata={
                k: str(v)
                for k, v in event.metadata.items()
                if k not in {"summary", "capability", "operation", "provider", "tier",
                              "requires_approval", "steps", "risk", "confidence"}
            },
            ts=atlas.clock.now().isoformat(),
        )

    atlas.bus.subscribe("orchestrator", _on_orchestrator_event)

    # SSE connections subscribe per-task; we store queues in a shared dict.
    # Key: task_id → list of asyncio.Queue instances (one per active SSE client)
    sse_queues: dict[str, list[asyncio.Queue[str | None]]] = {}

    async def _on_orchestrator_event_sse(event: Event) -> None:
        if not isinstance(event, OrchestratorEvent):
            return
        queues = sse_queues.get(event.task_id, [])
        for q in queues:
            await q.put(event.task_id)  # signal: new event available for this task

    atlas.bus.subscribe("orchestrator", _on_orchestrator_event_sse)

    app.state.atlas = atlas
    app.state.event_store = event_store
    app.state.sse_queues = sse_queues
    app.state.version = version("atlas")
    app.state.active_task_count = 0
    app.state.active_task_lock = asyncio.Lock()
    yield
    await atlas.close()


def create_app() -> FastAPI:
    """Create and configure the ATLAS FastAPI application."""
    app = FastAPI(
        title="ATLAS API",
        version="1",
        description="ATLAS — Autonomous Task & Learning Agent System control API",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )

    # CORS — dev only: allow the Next.js dev server
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    # Global exception handler — maps known Atlas exceptions to stable codes
    app.add_exception_handler(Exception, atlas_exception_handler)

    # Register routers (imported here to keep the factory free of circular deps)
    from atlas.interfaces.api.routes_runtime import router as runtime_router
    from atlas.interfaces.api.routes_tasks import router as tasks_router
    from atlas.interfaces.api.routes_approvals import router as approvals_router
    from atlas.interfaces.api.routes_capabilities import router as capabilities_router
    from atlas.interfaces.api.routes_trust import router as trust_router
    from atlas.interfaces.api.events import router as events_router

    # Each API path now has exactly one owning router — see routes_tasks.py
    # and routes_trust.py module docstrings/comments for the split:
    #   - tasks_router:      POST /tasks, POST /tasks/{id}/cancel, GET /tasks/{id}/events
    #   - approvals_router:  GET /approvals/pending, POST /approvals/{id}/decide
    #   - trust_router:      GET /tasks, GET /tasks/{id}, GET /approvals/{id},
    #                        GET/POST /memory/*, GET /audit
    # trust_router still declares prefix="/api/v1" internally (see routes_trust.py),
    # so it is mounted with an empty prefix here to avoid doubling it.
    app.include_router(runtime_router, prefix="/api/v1")
    app.include_router(tasks_router, prefix="/api/v1")
    app.include_router(approvals_router, prefix="/api/v1")
    app.include_router(capabilities_router, prefix="/api/v1")
    app.include_router(trust_router, prefix="")
    app.include_router(events_router, prefix="/api/v1")

    return app
