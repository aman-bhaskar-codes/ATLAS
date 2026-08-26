"""Errors must be logged server-side and must not be miscategorised.

TWO BUGS THIS PINS:

1. ``errors.py`` had NO logging on any path. A 500 returned "unexpected server
   error" to the client and left nothing behind on the server, so the only record
   of a crash was whatever the client happened to report. Debugging a failure
   meant reproducing it.

2. ``KeyError`` was mapped to 404. Any stray dict miss anywhere below the API —
   ``metadata["provider"]``, a config lookup, a registry key — was reported to the
   client as "not found". A plausible-looking status code is worse than a 500: it
   tells the frontend the request was fine and the entity simply is not there, so
   the bug is never investigated. Only an explicit ``NotFoundError`` means that.

The logger is patched rather than captured with ``structlog.testing.capture_logs``
because logging is configured with ``cache_logger_on_first_use=True``: the
module-level proxies bind their processor chain on first use, so a later
reconfigure does not reach them. Patching the module's ``_log`` asserts the same
contract — the code path emitted one record, with these fields — without
depending on structlog internals.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from httpx import AsyncClient

from atlas.interfaces.api import app as app_module
from atlas.interfaces.api import errors as errors_module
from tests.api.conftest import app_client

_TARGET = "/api/v1/runtime/status"


async def test_unmapped_exception_is_logged_exactly_once(tmp_path: Path) -> None:
    """A 500 leaves a server-side record, with the request id to correlate on."""
    from atlas.interfaces.api.facade import DefaultAtlasControlPlane

    log = MagicMock()
    async with app_client(tmp_path) as (_app, client):
        with (
            patch.object(app_module, "_log", log),
            patch.object(
                DefaultAtlasControlPlane,
                "runtime_status",
                AsyncMock(side_effect=RuntimeError("boom")),
            ),
        ):
            response = await client.get(_TARGET, headers={"X-Request-ID": "rid-500"})

    assert response.status_code == 500
    assert log.exception.call_count == 1, f"expected one error record, got {log.exception.call_count}"
    event, kwargs = log.exception.call_args.args[0], log.exception.call_args.kwargs
    assert event == "api.unhandled_exception"
    assert kwargs["path"] == _TARGET
    assert kwargs["request_id"] == "rid-500", "the log cannot be correlated with the response"


async def test_the_500_body_still_says_nothing(tmp_path: Path) -> None:
    """Logging server-side must not change what the client is told."""
    from atlas.interfaces.api.facade import DefaultAtlasControlPlane

    async with app_client(tmp_path) as (_app, client):
        with patch.object(
            DefaultAtlasControlPlane,
            "runtime_status",
            AsyncMock(side_effect=RuntimeError("/etc/atlas/secrets.yaml is unreadable")),
        ):
            response = await client.get(_TARGET)

    assert response.json()["detail"] == "unexpected server error"
    assert "secrets.yaml" not in response.text
    assert "Traceback" not in response.text


async def test_mapped_error_is_logged_as_a_warning(tmp_path: Path) -> None:
    """4xx/503 are expected outcomes, so they warn rather than page anyone."""
    from atlas.interfaces.api.facade import DefaultAtlasControlPlane

    log = MagicMock()
    async with app_client(tmp_path) as (_app, client):
        with (
            patch.object(errors_module, "_log", log),
            patch.object(
                DefaultAtlasControlPlane,
                "runtime_status",
                AsyncMock(side_effect=errors_module.NotFoundError("nope")),
            ),
        ):
            response = await client.get(_TARGET)

    assert response.status_code == 404
    assert log.warning.call_count == 1
    kwargs = log.warning.call_args.kwargs
    assert kwargs["code"] == "not_found"
    assert kwargs["status"] == 404
    assert kwargs["exc_type"] == "NotFoundError"


async def test_a_bare_keyerror_is_a_500_not_a_404(tmp_path: Path) -> None:
    """THE regression: an unrelated dict miss must not be reported as "not found"."""
    from atlas.interfaces.api.facade import DefaultAtlasControlPlane

    async with app_client(tmp_path) as (_app, client):
        with patch.object(
            DefaultAtlasControlPlane,
            "runtime_status",
            AsyncMock(side_effect=KeyError("provider")),
        ):
            response = await client.get(_TARGET)

    assert response.status_code == 500, (
        "a stray KeyError is being reported as 404 — the client is told the "
        "entity does not exist and the real bug is never investigated"
    )
    assert response.json()["error"] == "internal_error"


async def test_notfounderror_is_still_a_404(api_client: AsyncClient) -> None:
    """The narrowing must not have broken the genuine case."""
    response = await api_client.get("/api/v1/tasks/definitely-not-a-real-task")

    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


async def test_notfounderror_detail_is_truncated(tmp_path: Path) -> None:
    """``detail`` is bounded at 200 chars so an exception cannot become a payload."""
    from atlas.interfaces.api.facade import DefaultAtlasControlPlane

    async with app_client(tmp_path) as (_app, client):
        with patch.object(
            DefaultAtlasControlPlane,
            "runtime_status",
            AsyncMock(side_effect=errors_module.NotFoundError("x" * 5000)),
        ):
            response = await client.get(_TARGET)

    assert len(response.json()["detail"]) == 200
