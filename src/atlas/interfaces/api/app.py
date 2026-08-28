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

WHY the middleware order at the bottom of `create_app` matters: Starlette builds
its stack as [ServerErrorMiddleware, *user_middleware, ExceptionMiddleware] and
`add_middleware` inserts at index 0, so the LAST middleware added is the
OUTERMOST. CORS is therefore added last — anything added before it produces
responses that travel back out through CORS and arrive at the browser with
`Access-Control-Allow-Origin`. Anything added after it (or handled by
ServerErrorMiddleware, which is outermost of all) does not, and the browser
reports it as an opaque `TypeError: Failed to fetch` with no status.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from importlib.metadata import version
from uuid import uuid4

from fastapi import Depends, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from atlas.app import build
from atlas.infra.bus import Event
from atlas.infra.logging import bind_context, clear_context, get_logger
from atlas.infra.tasks import spawn
from atlas.interfaces.api.errors import error_envelope, internal_error_response, register_exception_handlers

_log = get_logger("atlas.api.app")

# Methods a readonly ('ro:') key may use. Everything else mutates.
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _reject_readonly_mutation(request: Request) -> Response | None:
    """403 when a readonly key attempts a mutating method, else None.

    WHY here and not as a route dependency: auth.py's contract says "READONLY
    keys (prefixed 'ro:') can read but never mutate", and nothing enforced it —
    the role was parsed and then ignored, so the documented guarantee was false.
    Enforcing it in the single middleware makes it true in one place instead of
    needing `Depends(require_admin)` threaded onto every mutating route, where a
    forgotten route silently becomes a hole.

    Trade-off accepted: because middleware runs before routing, a readonly key
    POSTing to a nonexistent path gets 403 rather than 404. That is the safer of
    the two, since it does not disclose which routes exist.

    Local mode is unaffected: ANONYMOUS_LOCAL has role 'admin'.
    """
    if request.method in _SAFE_METHODS:
        return None
    principal = getattr(request.state, "principal", None)
    if principal is None or getattr(principal, "role", None) != "readonly":
        return None
    return error_envelope(
        request,
        code="readonly_key",
        detail="read-only key cannot perform mutations",
        status=403,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build and start Atlas on startup; close it on shutdown.

    The body is wrapped in try/finally: without it, a failure anywhere between
    `build()` and `yield` leaves the Atlas object — database connections, the
    ChromaDB client, the health-monitor loop — started and never closed, because
    the shutdown half of an @asynccontextmanager lifespan only runs if startup
    reached the yield.
    """
    from atlas.interfaces.api import routes_events
    from atlas.interfaces.api.event_store import TaskEventStore
    from atlas.interfaces.api.websocket import ConnectionManager, EventBroadcaster
    from atlas.orchestration.events import OrchestratorEvent

    atlas = await build()
    ws_broadcaster: EventBroadcaster | None = None
    memory_ws_broadcaster: object | None = None

    try:
        await atlas.start()

        # Run a startup backup in the background. Tracked so it cannot be
        # garbage-collected mid-write.
        from atlas.infra.backup import create_backup

        spawn(create_backup(atlas.settings), name="atlas-startup-backup")

        # ── Phase 3: wire event bus into each memory subsystem so MemoryEvents
        #    are emitted in real-time when episodes/facts/prefs change ──────────
        # NOTE: These are now called in Atlas.start() so they work in ALL execution
        # paths (CLI, API, tests). No need to call them again here.

        event_store = TaskEventStore(atlas.db)

        # Initialize Trigger Engine (Phase 3)
        from atlas.autonomy.automations import AutomationRegistry
        from atlas.autonomy.trigger_engine import TriggerEngine

        automation_registry = AutomationRegistry(atlas.db)
        trigger_engine = TriggerEngine(automation_registry)
        atlas.bus.subscribe_global(trigger_engine.handle_event)

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
        routes_events.set_dependencies(ws_manager, atlas.db, atlas.bus)

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
        app.state.startup_time = datetime.now(UTC)  # For liveness probe
        yield
    finally:
        # Each teardown step is isolated: a broadcaster that fails to stop must
        # not prevent atlas.close() from releasing the database and Chroma client.
        for name, broadcaster in (
            ("memory_ws_broadcaster", memory_ws_broadcaster),
            ("ws_broadcaster", ws_broadcaster),
        ):
            if broadcaster is None:
                continue
            try:
                await broadcaster.stop()  # type: ignore[attr-defined]
            except Exception:
                _log.exception("lifespan.broadcaster_stop_failed", event_type="lifecycle", component=name)
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

    # Global exception handlers — one per concrete Atlas exception type, so they
    # are installed on ExceptionMiddleware (innermost) and their responses travel
    # back out through CORS. See errors.py for why a blanket Exception handler
    # cannot work here.
    register_exception_handlers(app)

    # Batch 7: transport-level identity. No keys configured => local open mode.
    from atlas.infra.config import load_settings as _load_settings
    from atlas.interfaces.api.auth import parse_api_keys, require_principal, resolve_principal
    from atlas.interfaces.api.rate_limit import TokenBucketLimiter, rate_limit

    keys_env = os.environ.get("ATLAS_API_KEYS")
    try:
        _settings = _load_settings()
        app.state.api_keys = parse_api_keys(getattr(_settings, "api_keys", None))
    except Exception as exc:
        # Fail CLOSED when the operator clearly intended authentication: silently
        # falling back to {} would hand them an unauthenticated server. With no
        # keys configured at all, an unreadable settings file is the ordinary
        # local case and must not block startup.
        if keys_env:
            raise RuntimeError(
                "ATLAS_API_KEYS is set but settings failed to load "
                f"({type(exc).__name__}); refusing to start unauthenticated"
            ) from exc
        # Log the exception TYPE only — the message can quote config values.
        _log.warning("api.settings_load_failed", event_type="api", exc_type=type(exc).__name__)
        app.state.api_keys = {}

    # The second half of failing closed: settings can load fine and still yield an
    # EMPTY key map — ATLAS_API_KEYS=",,," or "ro:" both parse to nothing. That
    # combination previously produced a silently open server for an operator who
    # had explicitly configured authentication, which is the worst possible
    # outcome. Neither the message nor any log line quotes the value.
    if keys_env and not app.state.api_keys:
        raise RuntimeError(
            "ATLAS_API_KEYS is set but contains no usable keys; refusing to start "
            "unauthenticated (expected a comma-separated list, 'ro:' prefix for read-only)"
        )
    # Rate-limit quotas are env-tunable; the defaults preserve production
    # behavior (120 burst, 60/min refill). Overriding via env lets local/E2E
    # runs lift the ceiling so a fresh browser context's CORS preflights and
    # background polling are never throttled — the bucket is an abuse control,
    # not something functional tests should be coupled to.
    _rl_capacity_raw = os.environ.get("ATLAS_RATE_LIMIT_CAPACITY")
    _rl_refill_raw = os.environ.get("ATLAS_RATE_LIMIT_PER_MINUTE")
    try:
        _rl_capacity = int(_rl_capacity_raw) if _rl_capacity_raw else 120
        _rl_refill = float(_rl_refill_raw) if _rl_refill_raw else 60.0
    except ValueError:
        _rl_capacity, _rl_refill = 120, 60.0
    app.state.rate_limiter = TokenBucketLimiter(capacity=_rl_capacity, refill_per_minute=_rl_refill)

    # ── The single request middleware ───────────────────────────────────────────
    # Request id, quota identity, throttling and the last-resort error envelope
    # all live in ONE layer. Reasons for one and not four:
    #  * each BaseHTTPMiddleware wraps the response body in an anyio task pair,
    #    which is measurable overhead on an SSE stream that stays open for
    #    minutes; and
    #  * the catch-all MUST be inside CORS. An exception that escapes to
    #    ServerErrorMiddleware (outermost of all, always) produces a 500 with no
    #    CORS headers, which the browser cannot distinguish from the backend
    #    being down.
    @app.middleware("http")
    async def _request_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or uuid4().hex
        request.state.request_id = request_id
        # Resolve identity here, not in the route dependency: the quota needs it
        # BEFORE routing, and middleware cannot reject (a raise here skips the
        # handler stack). require_principal is what actually returns 401.
        request.state.principal = resolve_principal(
            getattr(request.app.state, "api_keys", {}),
            request.headers.get("Authorization"),
        )
        bind_context(request_id=request_id)
        try:
            throttled = await rate_limit(request)
            if throttled is not None:
                return throttled
            denied = _reject_readonly_mutation(request)
            if denied is not None:
                return denied
            response = await call_next(request)
        except Exception:
            _log.exception(
                "api.unhandled_exception",
                event_type="api",
                method=request.method,
                path=request.url.path,
                request_id=request_id,
            )
            return internal_error_response(request)
        finally:
            clear_context()
        response.headers.setdefault("X-Request-ID", request_id)
        return response

    # CORS — dev only: allow the Next.js dev server. Added LAST so it is the
    # OUTERMOST user middleware: every response above, including 429s and the
    # 500 envelope, passes back out through it and gets its CORS headers.
    #
    # PUT and DELETE are listed because the automation routes use them
    # (`routes_automations.py`; `autonomyApi.updateAutomation` / `deleteAutomation`).
    # Without them the browser's preflight is refused and the Automations UI can
    # read but never edit — a dead control, not a visible error.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    # Register routers (imported here to keep the factory free of circular deps)
    from atlas.interfaces.api.events import router as events_router
    from atlas.interfaces.api.health import router as health_router  # Runtime health endpoints
    from atlas.interfaces.api.routes_approvals import router as approvals_router
    from atlas.interfaces.api.routes_attachments import router as attachments_router
    from atlas.interfaces.api.routes_automations import router as automations_router  # Phase 3
    from atlas.interfaces.api.routes_capabilities import router as capabilities_router
    from atlas.interfaces.api.routes_events import router as events_ws_router
    from atlas.interfaces.api.routes_feedback import router as feedback_router
    from atlas.interfaces.api.routes_knowledge import router as knowledge_router
    from atlas.interfaces.api.routes_learning import router as learning_router  # Batch 6
    from atlas.interfaces.api.routes_memory import router as memory_router
    from atlas.interfaces.api.routes_ops import router as ops_router  # Batch 6
    from atlas.interfaces.api.routes_providers import router as providers_router  # Zero-cost-first
    from atlas.interfaces.api.routes_runtime import router as runtime_router
    from atlas.interfaces.api.routes_tasks import router as tasks_router
    from atlas.interfaces.api.routes_trajectory import router as trajectory_router  # Phase 2
    from atlas.interfaces.api.routes_trust import router as trust_router
    from atlas.interfaces.api.routes_voice import router as voice_router  # Voice pipeline (optional)

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
    #
    # AUTH: `auth_required` attaches transport-level identity to every HTTP router.
    # In local mode (no ATLAS_API_KEYS) require_principal returns ANONYMOUS_LOCAL
    # and nothing changes; setting the env var is what starts enforcing 401.
    # Deliberately NOT applied to:
    #   * health_router — /live, /ready, /health must stay reachable for the
    #     container HEALTHCHECK and any external probe, which have no key.
    #   * memory_router, events_ws_router — these declare WebSocket routes, and
    #     require_principal takes a Request, which does not resolve for a
    #     WebSocket scope. WebSocket auth is tracked as debt, not faked here.
    auth_required = [Depends(require_principal)]

    app.include_router(health_router, prefix="/api/v1")  # Runtime health endpoints (probes: no auth)
    app.include_router(runtime_router, prefix="/api/v1", dependencies=auth_required)
    app.include_router(tasks_router, prefix="/api/v1", dependencies=auth_required)
    app.include_router(approvals_router, prefix="/api/v1", dependencies=auth_required)
    app.include_router(capabilities_router, prefix="/api/v1", dependencies=auth_required)
    app.include_router(feedback_router, prefix="/api/v1", dependencies=auth_required)
    app.include_router(knowledge_router, prefix="", dependencies=auth_required)  # already has /api/v1 prefix
    app.include_router(memory_router, prefix="")  # already has /api/v1 prefix and /ws prefix
    app.include_router(trajectory_router, prefix="", dependencies=auth_required)  # Phase 2: has /api/v1 prefix
    app.include_router(attachments_router, prefix="/api/v1", dependencies=auth_required)
    app.include_router(trust_router, prefix="", dependencies=auth_required)
    app.include_router(events_router, prefix="/api/v1", dependencies=auth_required)
    app.include_router(events_ws_router, prefix="")  # WebSocket routes include /ws/ prefix
    app.include_router(learning_router, prefix="/api/v1", dependencies=auth_required)  # Batch 6
    app.include_router(ops_router, prefix="/api/v1", dependencies=auth_required)  # Batch 6
    app.include_router(providers_router, prefix="", dependencies=auth_required)  # already has /api/v1 prefix
    app.include_router(automations_router, prefix="", dependencies=auth_required)  # has /api/v1/automations prefix
    # Voice router carries a WebSocket route (/ws/voice), so — like memory_router
    # and events_ws_router — it is mounted WITHOUT the require_principal Request
    # dependency (which cannot resolve a WebSocket scope). Its HTTP endpoints
    # (/voice/speak, /voice/transcribe) are therefore not key-gated; WebSocket +
    # voice-HTTP auth is tracked as the same debt as the other WS routers. The
    # subsystem is off by default, so no live surface exists until enabled.
    app.include_router(voice_router, prefix="/api/v1")

    return app
