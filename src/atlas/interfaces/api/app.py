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
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import version
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from atlas.app import build
from atlas.infra.bus import Event
from atlas.interfaces.api.errors import atlas_exception_handler

if TYPE_CHECKING:
    pass


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build and start Atlas on startup; close it on shutdown."""
    from atlas.interfaces.api import routes_events
    from atlas.interfaces.api.event_store import TaskEventStore
    from atlas.interfaces.api.websocket import ConnectionManager, EventBroadcaster
    from atlas.orchestration.events import OrchestratorEvent

    atlas = await build()
    await atlas.start()

    # Run a startup backup in the background
    from atlas.infra.backup import create_backup

    asyncio.create_task(create_backup(atlas.settings))

    # ── Phase 3: wire event bus into each memory subsystem so MemoryEvents
    #    are emitted in real-time when episodes/facts/prefs change ──────────
    # NOTE: These are now called in Atlas.start() so they work in ALL execution
    # paths (CLI, API, tests). No need to call them again here.

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
                if k
                not in {
                    "summary",
                    "capability",
                    "operation",
                    "provider",
                    "tier",
                    "requires_approval",
                    "steps",
                    "risk",
                    "confidence",
                    "tool",
                    "args",
                    "ok",
                    "error",
                }
            },
            ts=atlas.clock.now().isoformat(),
        )

    atlas.bus.subscribe("orchestrator", _on_orchestrator_event)

    # Initialize WebSocket connection manager and broadcaster
    ws_manager = ConnectionManager()
    ws_broadcaster = EventBroadcaster(atlas.bus, ws_manager)
    ws_broadcaster.start()

    # ── Phase 3: dedicated memory WebSocket manager + broadcaster ──────────
    from atlas.interfaces.api.websocket import MemoryBroadcaster

    memory_ws_manager = ConnectionManager()
    memory_ws_broadcaster = MemoryBroadcaster(atlas.bus, memory_ws_manager)
    memory_ws_broadcaster.start()

    # Set dependencies for WebSocket routes
    routes_events.set_dependencies(ws_manager, atlas.db)

    # Set dependencies for memory routes
    from atlas.interfaces.api import routes_memory

    routes_memory.set_dependencies(memory_ws_manager, atlas.db, atlas)

    # Phase 2: Connect trajectory store to event bus
    if hasattr(atlas, "trajectory_store") and atlas.trajectory_store:
        atlas.trajectory_store.set_bus(atlas.bus)

    # SSE connections subscribe per-task; we store queues in a shared dict.
    # Key: task_id → list of asyncio.Queue instances (one per active SSE client)
    sse_queues: dict[str, list[asyncio.Queue[str | None]]] = {}

    async def _on_orchestrator_event_bus(event: Event) -> None:
        if not isinstance(event, OrchestratorEvent):
            return

        # Notify SSE clients
        queues = sse_queues.get(event.task_id, [])
        for q in queues:
            await q.put(event.task_id)  # signal: new event available for this task

    atlas.bus.subscribe("orchestrator", _on_orchestrator_event_bus)

    app.state.atlas = atlas
    app.state.event_store = event_store
    app.state.ws_manager = ws_manager
    app.state.ws_broadcaster = ws_broadcaster
    app.state.memory_ws_manager = memory_ws_manager
    app.state.memory_ws_broadcaster = memory_ws_broadcaster
    app.state.sse_queues = sse_queues
    app.state.version = version("atlas")
    app.state.active_task_count = 0
    app.state.active_task_lock = asyncio.Lock()
    yield
    await memory_ws_broadcaster.stop()
    await ws_broadcaster.stop()
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

    # Batch 7: transport-level identity. No keys configured => local open mode.
    from atlas.infra.config import load_settings as _load_settings
    from atlas.interfaces.api.auth import parse_api_keys

    try:
        _settings = _load_settings()
        app.state.api_keys = parse_api_keys(getattr(_settings, "api_keys", None))
    except Exception:
        app.state.api_keys = {}

    # Register routers (imported here to keep the factory free of circular deps)
    from atlas.interfaces.api.events import router as events_router
    from atlas.interfaces.api.routes_approvals import router as approvals_router
    from atlas.interfaces.api.routes_attachments import router as attachments_router
    from atlas.interfaces.api.routes_capabilities import router as capabilities_router
    from atlas.interfaces.api.routes_events import router as events_ws_router
    from atlas.interfaces.api.routes_feedback import router as feedback_router
    from atlas.interfaces.api.routes_knowledge import router as knowledge_router
    from atlas.interfaces.api.routes_learning import router as learning_router  # Batch 6
    from atlas.interfaces.api.routes_memory import router as memory_router
    from atlas.interfaces.api.routes_ops import router as ops_router  # Batch 6
    from atlas.interfaces.api.routes_runtime import router as runtime_router
    from atlas.interfaces.api.routes_tasks import router as tasks_router
    from atlas.interfaces.api.routes_trajectory import router as trajectory_router  # Phase 2
    from atlas.interfaces.api.routes_trust import router as trust_router

    # Each API path now has exactly one owning router — see routes_tasks.py
    # and routes_trust.py module docstrings/comments for the split:
    #   - tasks_router:      POST /tasks, POST /tasks/{id}/cancel, GET /tasks/{id}/events
    #   - approvals_router:  GET /approvals/pending, POST /approvals/{id}/decide
    #   - trust_router:      GET /tasks, GET /tasks/{id}, GET /approvals/{id},
    #                        GET/POST /memory/*, GET /audit
    #   - feedback_router:   POST /feedback, GET /feedback/stats,
    #                        GET /audit/verify, GET/POST /schedules
    #   - trajectory_router: GET /trajectory/* (Phase 2 learning endpoints)
    # trust_router still declares prefix="/api/v1" internally (see routes_trust.py),
    # so it is mounted with an empty prefix here to avoid doubling it.
    app.include_router(runtime_router, prefix="/api/v1")
    app.include_router(tasks_router, prefix="/api/v1")
    app.include_router(approvals_router, prefix="/api/v1")
    app.include_router(capabilities_router, prefix="/api/v1")
    app.include_router(feedback_router, prefix="/api/v1")
    app.include_router(knowledge_router, prefix="")  # already has /api/v1 prefix
    app.include_router(memory_router, prefix="")  # already has /api/v1 prefix and /ws prefix
    app.include_router(trajectory_router, prefix="")  # Phase 2: already has /api/v1 prefix
    app.include_router(attachments_router, prefix="/api/v1")
    app.include_router(trust_router, prefix="")
    app.include_router(events_router, prefix="/api/v1")
    app.include_router(events_ws_router, prefix="")  # WebSocket routes include /ws/ prefix
    app.include_router(learning_router, prefix="/api/v1")  # Batch 6
    app.include_router(ops_router, prefix="/api/v1")  # Batch 6

    return app
