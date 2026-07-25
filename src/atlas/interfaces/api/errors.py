"""Exception → stable HTTP error code mapping.

WHY: routes never leak tracebacks, internal module paths, or secret-bearing
exception messages. Every error becomes a stable JSON envelope the frontend
can pattern-match on.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse


async def atlas_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Map known Atlas exceptions to stable HTTP status codes."""
    # Import inline to avoid circular dependency at module load time
    from atlas.intelligence.errors import BudgetExceededError
    from atlas.safety.engine import DeniedError, HaltedError

    request_id = request.headers.get("X-Request-ID")

    mapping: list[tuple[type[Exception], int, str]] = [
        (DeniedError, 403, "denied"),
        (HaltedError, 503, "halted"),
        (BudgetExceededError, 402, "budget_exceeded"),
        (KeyError, 404, "not_found"),
    ]

    for exc_type, status, code in mapping:
        if isinstance(exc, exc_type):
            return JSONResponse(
                status_code=status,
                content={
                    "error": code,
                    "detail": str(exc)[:200],
                    "request_id": request_id,
                },
            )

    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_error",
            "detail": "unexpected server error",
            "request_id": request_id,
        },
    )
