"""Lightweight tracing. WHY: a Phase-1 causal timeline without an OTel
dependency. The span API mirrors OTel so a future exporter is a drop-in."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from atlas.infra.config import TracingCfg
from atlas.infra.logging import get_logger

_log = get_logger("atlas.tracing")


class Tracer:
    def __init__(self, cfg: TracingCfg) -> None:
        self._enabled = cfg.enabled

    @asynccontextmanager
    async def span(self, name: str, **attrs: str) -> AsyncIterator[None]:
        if not self._enabled:
            yield
            return
        start = time.perf_counter()
        try:
            yield
        finally:
            dur_ms = int((time.perf_counter() - start) * 1000)
            _log.debug("span", event_type="span", span=name, duration_ms=dur_ms, **attrs)
