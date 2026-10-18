from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from atlas.interfaces.api.routes_mcp import router


@pytest.mark.parametrize("kind", ["resources", "prompts"])
def test_mcp_inspection_does_not_connect_disconnected_server(kind: str) -> None:
    connections = []

    async def ensure_ready(server_id: str):
        connections.append(server_id)
        return SimpleNamespace(discover_resources=_discover, discover_prompts=_discover)

    async def _discover():
        return []

    manager = SimpleNamespace(ensure_ready=ensure_ready, connection=lambda _: SimpleNamespace(state="disconnected"))
    app = FastAPI()
    app.include_router(router)
    app.state.atlas = SimpleNamespace(mcp_manager=manager)
    response = TestClient(app).get(f"/mcp/servers/example/{kind}")
    assert response.status_code == 409
    assert connections == []
