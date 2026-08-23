"""WebSocket route contract — the working pair exists, the dead pair is gone.

Two WebSocket endpoints used to be declared in ``events.py``: ``/api/v1/events``
and ``/api/v1/tasks/{id}/events/ws``. Both read ``app.state.global_ws_queues`` /
``app.state.ws_queues`` — keys nothing in the codebase ever set and nothing ever
fed. They accepted the socket and then raised AttributeError on the first read,
with no test and no frontend consumer. They were duplicates of the pair in
``routes_events.py``, which IS backed by ConnectionManager/EventBroadcaster and
IS wired in the lifespan, so they were deleted rather than reimplemented.

This test pins both halves of that decision so neither can silently regress:
the working routes must stay reachable, and the dead ones must stay absent.

WHY starlette's TestClient here instead of the ``api_client`` fixture:
``httpx.ASGITransport`` cannot speak the WebSocket ASGI protocol at all. The
external mocks are reused from conftest so no real Ollama/Docker is touched.
"""

from __future__ import annotations

import json
from pathlib import Path

from starlette.testclient import TestClient

from atlas.interfaces.api.app import create_app
from tests.api.conftest import _external_mocks

_WORKING_GLOBAL = "/ws/events"
_WORKING_TASK = "/ws/tasks/{task_id}/stream"
_DELETED = ("/api/v1/events", "/api/v1/tasks/{task_id}/events/ws")


def _websocket_paths(app: object) -> set[str]:
    """Paths of every declared WebSocket route, including nested routers.

    WHY the recursion: this FastAPI version does NOT flatten `include_router`
    into `app.routes`. A real `create_app()` yields 4 plain `Route` objects (the
    docs/openapi endpoints) plus 17 `_IncludedRouter` wrappers that hold the
    child router on `.original_router` and the mount prefix on
    `.include_context.prefix`, and expose no `.path` of their own. A flat
    `{r.path for r in app.routes}` scan therefore returns the empty set and this
    whole contract would pass vacuously.

    Written defensively rather than against the private attribute names alone:
    anything exposing `.routes` is descended into, so the test keeps working if
    a future FastAPI goes back to flattening.
    """
    from starlette.routing import WebSocketRoute

    found: set[str] = set()

    def walk(routes: object, prefix: str) -> None:
        for route in routes:  # type: ignore[attr-defined]
            if isinstance(route, WebSocketRoute):
                found.add(prefix + route.path)
                continue
            child = getattr(route, "original_router", None)
            if child is not None:
                ctx = getattr(route, "include_context", None)
                walk(child.routes, prefix + getattr(ctx, "prefix", ""))
            elif hasattr(route, "routes"):  # Mount / bare APIRouter
                walk(route.routes, prefix + getattr(route, "path", ""))

    walk(app.routes, "")  # type: ignore[attr-defined]
    return found


def test_dead_websocket_routes_are_absent(tmp_path: Path) -> None:
    """The two queue-reading endpoints must not exist in any form."""
    with _external_mocks(tmp_path):
        app = create_app()
    declared = _websocket_paths(app)
    # Guard against a vacuous pass: if the walker ever stops finding routes, the
    # "absent" assertions below become meaningless.
    assert declared, "no WebSocket routes discovered at all — the route walker is broken"
    for path in _DELETED:
        assert path not in declared, f"{path} was deleted; it can only ever raise AttributeError"


def test_working_websocket_routes_are_declared(tmp_path: Path) -> None:
    with _external_mocks(tmp_path):
        app = create_app()
    declared = _websocket_paths(app)
    assert _WORKING_GLOBAL in declared
    assert _WORKING_TASK in declared


def test_global_stream_delivers_a_first_frame(tmp_path: Path) -> None:
    """The surviving global stream must accept and send real bytes.

    This is the assertion the deleted endpoints could never have passed.
    """
    with _external_mocks(tmp_path):
        app = create_app()
        with TestClient(app) as client, client.websocket_connect(_WORKING_GLOBAL) as ws:
            frame = ws.receive_text()

    payload = json.loads(frame)
    assert isinstance(payload, dict) and payload, "first frame must be a non-empty JSON object"


def test_task_scoped_stream_delivers_a_first_frame(tmp_path: Path) -> None:
    with _external_mocks(tmp_path):
        app = create_app()
        with TestClient(app) as client:
            path = _WORKING_TASK.format(task_id="task-does-not-exist")
            with client.websocket_connect(path) as ws:
                frame = ws.receive_text()

    payload = json.loads(frame)
    assert isinstance(payload, dict) and payload
