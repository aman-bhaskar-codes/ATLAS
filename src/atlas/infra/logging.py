"""Structured logging.

WHY structlog + contextvars: correlation_id/task_id must ride along on every
log line without threading them through every call. contextvars are async-safe
so concurrent tasks don't bleed context. Output is JSON in prod, pretty in dev.
"""

from __future__ import annotations

import logging
from typing import Any

import structlog

from atlas.infra.config import LoggingCfg


def configure_logging(cfg: LoggingCfg) -> None:
    """Idempotent configuration. Safe to call once at startup."""
    level = getattr(logging, cfg.level.upper(), logging.INFO)
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]
    if cfg.format == "json":
        processors.append(structlog.processors.EventRenamer("message"))
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(module: str) -> structlog.stdlib.BoundLogger:
    """Return a logger bound to a module name."""
    return structlog.get_logger(module=module)  # type: ignore[no-any-return]


def bind_context(**kv: str) -> None:
    """Bind correlation/task ids for the current async context."""
    structlog.contextvars.bind_contextvars(**kv)


def clear_context() -> None:
    structlog.contextvars.clear_contextvars()
