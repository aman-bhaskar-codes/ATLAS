"""Exception → stable HTTP error code mapping.

WHY: routes never leak tracebacks, internal module paths, or secret-bearing
exception messages. Every error becomes a stable JSON envelope the frontend can
pattern-match on: ``{"error": code, "detail": ..., "request_id": ...}``.

WHY per-type handlers instead of one blanket ``Exception`` handler: Starlette
routes a handler registered for ``Exception`` (or 500) to
``ServerErrorMiddleware``, which is installed OUTSIDE every user middleware —
including CORS. A response produced there reaches the browser with no
``Access-Control-Allow-Origin`` header, so the fetch rejects with an opaque
``TypeError: Failed to fetch`` and the frontend can never see that it was, say,
a 403. Handlers registered for *concrete* exception types are installed on
``ExceptionMiddleware`` (innermost), so their responses travel back out through
the whole middleware stack and arrive CORS-annotated. The genuinely unexpected
case is caught by the request middleware in ``app.py`` for the same reason.
"""

from __future__ import annotations

import functools

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from atlas.infra.logging import get_logger

_log = get_logger("atlas.api.errors")


class NotFoundError(LookupError):
    """A requested entity does not exist. Maps to 404.

    WHY its own type: the previous mapping turned *any* ``KeyError`` into a 404,
    so an unrelated dict miss deep inside the orchestrator was reported to the
    client as "not found" — a plausible-looking status code that hid real bugs.
    Only an explicit raise of this type means "no such entity".
    """


def request_id_of(request: Request) -> str | None:
    """The id minted by the request middleware, else the caller-supplied header."""
    rid: str | None = getattr(request.state, "request_id", None)
    return rid or request.headers.get("X-Request-ID")


def error_envelope(
    request: Request,
    *,
    code: str,
    detail: str,
    status: int,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Build the stable error envelope, echoing the request id back to the caller."""
    request_id = request_id_of(request)
    out = dict(headers) if headers else {}
    if request_id:
        out["X-Request-ID"] = request_id
    return JSONResponse(
        status_code=status,
        content={"error": code, "detail": detail, "request_id": request_id},
        headers=out or None,
    )


def internal_error_response(request: Request) -> JSONResponse:
    """The 500 envelope. Deliberately says nothing about the cause."""
    return error_envelope(request, code="internal_error", detail="unexpected server error", status=500)


async def _handle_mapped(request: Request, exc: Exception, *, status: int, code: str) -> JSONResponse:
    _log.warning(
        "api.request_failed",
        event_type="api",
        code=code,
        status=status,
        method=request.method,
        path=request.url.path,
        exc_type=type(exc).__name__,
        request_id=request_id_of(request),
    )
    return error_envelope(request, code=code, detail=str(exc)[:200], status=status)


def register_exception_handlers(app: FastAPI) -> None:
    """Install one handler per concrete exception type.

    Imports are inline: ``atlas.safety`` and ``atlas.intelligence`` sit below
    ``atlas.interfaces`` in the layering, and importing them at module load time
    would drag the whole domain into every import of this module.
    """
    from atlas.intelligence.errors import BudgetExceededError
    from atlas.safety.engine import DeniedError, HaltedError

    mapping: list[tuple[type[Exception], int, str]] = [
        (DeniedError, 403, "denied"),
        (HaltedError, 503, "halted"),
        (BudgetExceededError, 402, "budget_exceeded"),
        (NotFoundError, 404, "not_found"),
    ]

    for exc_type, status, code in mapping:
        # functools.partial rather than a closure: a closure over the loop
        # variables would resolve to the last pair for every handler, and
        # Starlette's is_async_callable() unwraps partials, so the handler is
        # still correctly detected as a coroutine function and awaited.
        app.add_exception_handler(
            exc_type,
            functools.partial(_handle_mapped, status=status, code=code),
        )
