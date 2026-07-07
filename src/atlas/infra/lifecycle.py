"""Lifecycle manager.

WHY rollback on start failure: a partially-started system is an undefined state.
If service N fails to start, we stop the N-1 already started (reverse order) and
abort. Shutdown is best-effort-complete: every stop() is attempted even if one
raises, because leaking a resource is worse than an ugly log.
"""

from __future__ import annotations

from atlas.infra.logging import get_logger
from atlas.infra.registry import Service, ServiceRegistry

_log = get_logger("atlas.lifecycle")


class Lifecycle:
    def __init__(self, registry: ServiceRegistry) -> None:
        self._registry = registry
        self._started: list[tuple[str, Service]] = []

    async def start(self) -> None:
        order = self._registry.ordered()
        _log.info("lifecycle.start", event_type="lifecycle", order=[n for n, _ in order])
        for name, svc in order:
            try:
                await svc.start()
                self._started.append((name, svc))
            except Exception as exc:
                _log.error(
                    "service.start_failed", event_type="lifecycle", service=name, error=repr(exc)
                )
                await self.stop()
                raise

    async def stop(self) -> None:
        for name, svc in reversed(self._started):
            try:
                await svc.stop()
            except Exception as exc:
                _log.error(
                    "service.stop_failed", event_type="lifecycle", service=name, error=repr(exc)
                )
        self._started.clear()

    async def restart(self) -> None:
        await self.stop()
        await self.start()
