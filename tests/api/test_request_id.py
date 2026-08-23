"""Every response carries a correlatable request id.

THE BUG THIS PINS: ``X-Request-ID`` was already exposed through CORS
(``expose_headers``) and already a field in the error envelope — but nothing ever
generated one. The middleware only *read* an inbound header, so in practice the
field was ``null`` on every response and the header was absent. A user reporting
"it failed" had nothing to hand over, and the server log had nothing to join on.

So the contract is three-part: mint one when absent, honour one when supplied, and
make the envelope's ``request_id`` equal the header on the SAME response — an id
in the body that differs from the header is worse than none.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from tests.api.conftest import app_client

_TARGET = "/api/v1/runtime/status"


async def test_a_request_id_is_generated_when_absent(api_client: AsyncClient) -> None:
    response = await api_client.get(_TARGET)

    assert response.status_code == 200
    assert response.headers.get("x-request-id"), "no id was minted — nothing to correlate on"


async def test_generated_ids_differ_per_request(api_client: AsyncClient) -> None:
    """A constant id would correlate everything with everything."""
    first = (await api_client.get(_TARGET)).headers["x-request-id"]
    second = (await api_client.get(_TARGET)).headers["x-request-id"]

    assert first != second


async def test_an_inbound_request_id_is_echoed_unchanged(api_client: AsyncClient) -> None:
    """The caller's id wins, so a browser-side trace id survives the round trip."""
    response = await api_client.get(_TARGET, headers={"X-Request-ID": "caller-supplied-id"})

    assert response.headers["x-request-id"] == "caller-supplied-id"


async def test_the_500_envelope_matches_its_own_header(tmp_path: Path) -> None:
    """Body and header must agree, or the id the user reports points nowhere."""
    from atlas.interfaces.api.facade import DefaultAtlasControlPlane

    async with app_client(tmp_path) as (_app, client):
        with patch.object(
            DefaultAtlasControlPlane, "runtime_status", AsyncMock(side_effect=RuntimeError("boom"))
        ):
            response = await client.get(_TARGET)

    assert response.status_code == 500
    body_id = response.json()["request_id"]
    assert body_id, "the envelope's request_id is still null"
    assert body_id == response.headers.get("x-request-id")


async def test_the_404_envelope_matches_its_own_header(api_client: AsyncClient) -> None:
    """Mapped handlers run on ExceptionMiddleware, a different path to the 500."""
    response = await api_client.get("/api/v1/tasks/not-a-task")

    assert response.status_code == 404
    body_id = response.json()["request_id"]
    assert body_id
    assert body_id == response.headers.get("x-request-id")


async def test_the_429_envelope_matches_its_own_header(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The third path: returned from inside the middleware, before ``call_next``."""
    async with app_client(
        tmp_path,
        monkeypatch,
        ATLAS_RATE_LIMIT_CAPACITY="1",
        ATLAS_RATE_LIMIT_PER_MINUTE="0",
    ) as (_app, client):
        await client.get(_TARGET)
        throttled = await client.get(_TARGET, headers={"X-Request-ID": "rid-429"})

    assert throttled.status_code == 429
    assert throttled.json()["request_id"] == "rid-429"
    assert throttled.headers.get("x-request-id") == "rid-429"
