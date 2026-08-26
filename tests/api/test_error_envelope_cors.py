"""Every error status must reach the browser CORS-annotated.

THE BUG THIS PINS: ``CORSMiddleware`` used to be added FIRST and the rate
limiter second. Starlette builds its stack as
``[ServerErrorMiddleware, *user_middleware, ExceptionMiddleware]`` and
``add_middleware`` inserts at index 0, so the LAST middleware added is the
OUTERMOST — meaning the limiter wrapped CORS, and its 429 never passed through
it. Worse, the catch-all was registered for ``Exception``, which Starlette
installs on ``ServerErrorMiddleware`` — outermost of all, always, and therefore
never wrappable by CORS at any registration order.

The consequence was invisible to every server-side test: the status code and JSON
body were correct on the wire, but with no ``Access-Control-Allow-Origin`` header
the browser refuses to hand the response to the caller. ``fetch()`` rejects with
an opaque ``TypeError: Failed to fetch`` carrying no status, so the frontend
cannot tell a 403 from a 429 from the backend being down. That is the real origin
of the E2E "Failed to fetch" that was previously papered over by raising the
rate-limit ceiling.

So these tests assert on a HEADER, not on the status code — the status codes
already worked.

Coverage of the three distinct paths a response can take out of the app:
  * mapped domain exception  -> ExceptionMiddleware (innermost)  -> 403/503/402/404
  * middleware short-circuit -> returned before ``call_next``    -> 429
  * unmapped exception       -> caught in the request middleware -> 500
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from atlas.infra.types import SafetyDecision
from atlas.intelligence.errors import BudgetExceededError
from atlas.safety.engine import DeniedError, HaltedError
from tests.api.conftest import app_client

_ORIGIN = {"Origin": "http://localhost:3000"}
_CORS_HEADER = "access-control-allow-origin"

# Any authenticated GET works; /runtime/status is the one the status pill polls,
# so a missing CORS header here is exactly what blanks the UI.
_TARGET = "/api/v1/runtime/status"


def _denied() -> DeniedError:
    return DeniedError(SafetyDecision(decision="deny", tier=4, reason="blocked by policy", matched_rule="test-rule"))


# (exception, expected status, expected envelope code)
_MAPPED: list[tuple[Exception, int, str]] = [
    (_denied(), 403, "denied"),
    (HaltedError("kill switch engaged"), 503, "halted"),
    (BudgetExceededError("daily cap reached"), 402, "budget_exceeded"),
]


@pytest.mark.parametrize(("exc", "status", "code"), _MAPPED, ids=lambda v: str(v)[:24])
async def test_mapped_domain_errors_carry_cors(
    tmp_path: Path,
    exc: Exception,
    status: int,
    code: str,
) -> None:
    """403 / 503 / 402 travel back out through CORS, not around it."""
    from atlas.interfaces.api.facade import DefaultAtlasControlPlane

    async with app_client(tmp_path) as (_app, client):
        with patch.object(DefaultAtlasControlPlane, "runtime_status", AsyncMock(side_effect=exc)):
            response = await client.get(_TARGET, headers=_ORIGIN)

    assert response.status_code == status
    assert response.json()["error"] == code
    assert response.headers.get(_CORS_HEADER) == "http://localhost:3000", (
        f"{status} reached the browser without CORS — fetch() would see only "
        "'TypeError: Failed to fetch' with no status"
    )


async def test_not_found_carries_cors(api_client: AsyncClient) -> None:
    """404 from a real route (no patching): the frontend must be able to read it."""
    response = await api_client.get("/api/v1/tasks/task-does-not-exist", headers=_ORIGIN)

    assert response.status_code == 404
    assert response.json()["error"] == "not_found"
    assert response.headers.get(_CORS_HEADER) == "http://localhost:3000"


async def test_unmapped_exception_returns_500_with_cors(tmp_path: Path) -> None:
    """The path that previously could NOT be fixed by reordering middleware.

    An exception with no registered handler must be caught by the request
    middleware — INSIDE CORS — and never escape to ServerErrorMiddleware.
    """
    from atlas.interfaces.api.facade import DefaultAtlasControlPlane

    boom = AsyncMock(side_effect=RuntimeError("secret-bearing internal detail"))
    async with app_client(tmp_path) as (_app, client):
        with patch.object(DefaultAtlasControlPlane, "runtime_status", boom):
            response = await client.get(_TARGET, headers=_ORIGIN)

    assert response.status_code == 500
    body = response.json()
    assert body["error"] == "internal_error"
    assert body["detail"] == "unexpected server error"
    assert "secret-bearing" not in response.text, "the 500 envelope must not leak the cause"
    assert response.headers.get(_CORS_HEADER) == "http://localhost:3000"


async def test_rate_limited_response_carries_cors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """429 is returned from inside the middleware, before ``call_next``.

    Capacity 1 with zero refill makes this deterministic: request one spends the
    only token, request two is throttled.
    """
    async with app_client(
        tmp_path,
        monkeypatch,
        ATLAS_RATE_LIMIT_CAPACITY="1",
        ATLAS_RATE_LIMIT_PER_MINUTE="0",
    ) as (_app, client):
        first = await client.get(_TARGET, headers=_ORIGIN)
        second = await client.get(_TARGET, headers=_ORIGIN)

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"] == "rate_limited"
    assert second.headers.get("retry-after"), "a throttled caller must be told when to retry"
    assert second.headers.get(_CORS_HEADER) == "http://localhost:3000"


async def test_stream_for_unknown_task_fails_before_the_connected_frame(
    api_client: AsyncClient,
) -> None:
    """SSE validates the task up front, so the failure is a readable status.

    An exception raised inside the body generator happens AFTER the response head
    is on the wire — past every middleware, past CORS — so the browser would see
    a 200 that truncates mid-stream. ``stream_task_events`` therefore awaits
    ``get_task`` before constructing the StreamingResponse.
    """
    response = await api_client.get("/api/v1/tasks/task-does-not-exist/events/stream", headers=_ORIGIN)

    assert response.status_code == 404
    assert "connected" not in response.text, "the stream opened before validating the task"
    assert response.headers.get(_CORS_HEADER) == "http://localhost:3000"


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
async def test_preflight_allows_the_automation_mutation_methods(
    api_client: AsyncClient,
    method: str,
) -> None:
    """PUT/DELETE must survive the preflight, or the Automations UI is read-only.

    ``allow_methods`` was ``["GET", "POST"]`` while ``routes_automations`` serves
    ``PUT /api/v1/automations/{id}`` and ``DELETE /api/v1/automations/{id}``, which
    is what ``autonomyApi.updateAutomation``/``deleteAutomation`` call. A refused
    preflight is not an error the page can render — the request never leaves the
    browser — so enable/disable and delete were dead controls.

    Asserted on the preflight rather than the route because that is where the
    refusal happened; it needs no automation row to exist.
    """
    response = await api_client.options(
        "/api/v1/automations/any-id",
        headers={
            **_ORIGIN,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 200, f"{method} preflight refused: {response.text}"
    assert method in (response.headers.get("access-control-allow-methods") or "")
    assert response.headers.get(_CORS_HEADER) == "http://localhost:3000"
