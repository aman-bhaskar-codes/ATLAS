"""Transport authentication on mixed HTTP/WebSocket routers."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from atlas.interfaces.api import routes_events, routes_memory, routes_voice
from atlas.interfaces.api.websocket import ConnectionManager
from tests.api.conftest import app_client

_KEYS = "test-admin-key,ro:test-readonly-key"
_READ_PATHS = (
    "/api/v1/memory/episodes",
    "/api/v1/memory/facts",
    "/api/v1/memory/knowledge",
    "/api/v1/memory/knowledge/search?q=example",
    "/api/v1/memory/preferences",
    "/api/v1/memory/stats",
    "/api/v1/events/search",
    "/ws/stats",
)
_WS_PATHS = ("/ws/events", "/ws/tasks/task-1/stream", "/ws/memory/live", "/api/v1/ws/voice", "/generic")


@pytest.mark.parametrize("authorization", [None, "Bearer unknown"])
async def test_mixed_router_reads_reject_unidentified_callers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, authorization: str | None
) -> None:
    async with app_client(tmp_path, monkeypatch, ATLAS_API_KEYS=_KEYS) as (_app, client):
        for path in _READ_PATHS:
            headers = {"Authorization": authorization} if authorization else {}
            response = await client.get(path, headers=headers)
            assert response.status_code == 401, path


@pytest.mark.parametrize("key", ["test-admin-key", "test-readonly-key", None])
async def test_mixed_router_reads_accept_identified_or_local_callers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: str | None
) -> None:
    async with app_client(tmp_path, monkeypatch, ATLAS_API_KEYS=_KEYS if key else "") as (_app, client):
        for path in _READ_PATHS:
            headers = {"Authorization": f"Bearer {key}"} if key else {}
            assert (await client.get(path, headers=headers)).status_code == 200, path


async def test_webhook_requires_identity_when_api_keys_are_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with app_client(tmp_path, monkeypatch, ATLAS_API_KEYS=_KEYS) as (_app, client):
        assert (await client.post("/api/v1/webhooks/example", json={})).status_code == 401


@pytest.fixture
def ws_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    app = FastAPI()
    app.state.api_keys = {"test-admin-key": "admin", "test-readonly-key": "readonly"}
    app.state.atlas = SimpleNamespace(voice_service=object())
    manager = ConnectionManager()
    monkeypatch.setattr(routes_events, "_deps", SimpleNamespace(manager=manager, db=None, bus=None))
    monkeypatch.setattr(routes_memory, "_deps", SimpleNamespace(manager=manager, atlas=SimpleNamespace()))
    app.include_router(routes_events.router)
    app.include_router(routes_memory.router)
    app.include_router(routes_voice.router, prefix="/api/v1")

    @app.websocket("/generic")
    async def generic(websocket: WebSocket) -> None:
        client_id = await manager.connect(websocket)
        try:
            await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            await manager.disconnect(client_id)

    return TestClient(app)


@pytest.mark.parametrize("path", _WS_PATHS)
@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer unknown"}])
def test_websockets_reject_missing_or_wrong_key(ws_client: TestClient, path: str, headers: dict[str, str]) -> None:
    with pytest.raises(WebSocketDisconnect) as exc:
        with ws_client.websocket_connect(path, headers=headers):
            pass
    assert exc.value.code == 1008


@pytest.mark.parametrize("path", _WS_PATHS)
@pytest.mark.parametrize("origin", ["https://evil.example", "null", "http://localhost:3000.evil.example"])
def test_websockets_reject_bad_origin_even_with_valid_key(ws_client: TestClient, path: str, origin: str) -> None:
    with pytest.raises(WebSocketDisconnect) as exc:
        with ws_client.websocket_connect(path, headers={"Authorization": "Bearer test-admin-key", "Origin": origin}):
            pass
    assert exc.value.code == 1008


@pytest.mark.parametrize("path", _WS_PATHS)
def test_websockets_accept_authorized_browser_and_native_clients(ws_client: TestClient, path: str) -> None:
    for headers in (
        {"Authorization": "Bearer test-admin-key"},
        {"Authorization": "Bearer test-admin-key", "Origin": "http://localhost:3000"},
    ):
        with ws_client.websocket_connect(path, headers=headers):
            pass


@pytest.mark.parametrize("path", _WS_PATHS)
def test_websockets_support_browser_subprotocol_credentials_without_echoing_key(
    ws_client: TestClient, path: str
) -> None:
    with ws_client.websocket_connect(
        path, headers={"Origin": "http://localhost:3000"}, subprotocols=["atlas", "atlas.api-key.test-admin-key"]
    ) as ws:
        assert ws.accepted_subprotocol == "atlas"


@pytest.mark.parametrize("path", _WS_PATHS)
def test_websockets_do_not_accept_query_string_credentials(ws_client: TestClient, path: str) -> None:
    with pytest.raises(WebSocketDisconnect) as exc:
        with ws_client.websocket_connect(f"{path}?token=test-admin-key"):
            pass
    assert exc.value.code == 1008


@pytest.mark.parametrize("path", _WS_PATHS)
def test_local_websockets_accept_anonymous_clients_but_validate_browser_origin(
    ws_client: TestClient, path: str
) -> None:
    ws_client.app.state.api_keys = {}  # type: ignore[union-attr]
    for headers in ({}, {"Origin": "http://localhost:3000"}):
        with ws_client.websocket_connect(path, headers=headers):
            pass
    with pytest.raises(WebSocketDisconnect) as exc:
        with ws_client.websocket_connect(path, headers={"Origin": "https://evil.example"}):
            pass
    assert exc.value.code == 1008


@pytest.mark.parametrize("path", _WS_PATHS[:3])
def test_readonly_key_can_stream_observations(ws_client: TestClient, path: str) -> None:
    with ws_client.websocket_connect(path, headers={"Authorization": "Bearer test-readonly-key"}):
        pass


def test_readonly_key_cannot_run_voice_tasks(ws_client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect) as exc:
        with ws_client.websocket_connect("/api/v1/ws/voice", headers={"Authorization": "Bearer test-readonly-key"}):
            pass
    assert exc.value.code == 1008


def test_readonly_key_cannot_ingest_webhooks(ws_client: TestClient) -> None:
    response = ws_client.post(
        "/api/v1/webhooks/example", json={}, headers={"Authorization": "Bearer test-readonly-key"}
    )
    assert response.status_code == 403


@pytest.mark.parametrize(
    "protocols",
    [
        ["atlas", "atlas.api-key."],
        ["atlas", "atlas.api-key.unknown"],
        ["atlas", "atlas.api-key.test-admin-key", "atlas.api-key.unknown"],
        ["atlas", "atlas.api-key.test-admin-key", "atlas.api-key.test-admin-key"],
    ],
)
def test_websocket_rejects_missing_or_ambiguous_subprotocol_key(ws_client: TestClient, protocols: list[str]) -> None:
    with pytest.raises(WebSocketDisconnect) as exc:
        with ws_client.websocket_connect("/generic", subprotocols=protocols):
            pass
    assert exc.value.code == 1008


def test_invalid_authorization_does_not_fall_back_to_browser_credentials(ws_client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect) as exc:
        with ws_client.websocket_connect(
            "/generic", headers={"Authorization": "Basic invalid"}, subprotocols=["atlas.api-key.test-admin-key"]
        ):
            pass
    assert exc.value.code == 1008


@pytest.mark.parametrize("path", _WS_PATHS[:3])
def test_websocket_auth_runs_before_accessing_uninitialized_dependencies(
    ws_client: TestClient, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    monkeypatch.setattr(routes_events, "_deps", None)
    monkeypatch.setattr(routes_memory, "_deps", None)
    with pytest.raises(WebSocketDisconnect) as exc:
        with ws_client.websocket_connect(path):
            pass
    assert exc.value.code == 1008
