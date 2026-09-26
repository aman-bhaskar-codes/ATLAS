"""ToolingFabric — the high-level object the composition graph exposes.

Owns the registry and the executor; Parts 2+ add catalog/router here. Only
real components exist (Part 1 §62) — no placeholder properties.

``initialize`` implements the lifecycle (§37/§38/§56): register -> initialize ->
ready, with failure isolation — one broken adapter marks ITS tools FAILED and
never prevents unrelated tooling from starting. No fake readiness: a tool is
READY only after its adapter initialized and validated that definition.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from atlas.infra.logging import get_logger
from atlas.tooling.errors import ToolingError
from atlas.tooling.execution.executor import ToolingExecutor
from atlas.tooling.models.tool_health import ToolRuntimeState
from atlas.tooling.registry.registry import ToolingRegistry

_log = get_logger("atlas.tooling.fabric")


@dataclass
class InitializationReport:
    """Outcome of fabric startup, ready for logs and future health surfaces."""

    ready: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    failure_details: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.failed


class ToolingFabric:
    def __init__(
        self,
        *,
        registry: ToolingRegistry,
        executor: ToolingExecutor,
        catalog: Any | None = None,
    ) -> None:
        self.registry = registry
        self.executor = executor
        # Part 2: the persistent ToolCatalog, when built with a Database.
        # Typed as Any here to keep the Part-1 fabric importable without the
        # catalog package; bootstrap always passes a ToolCatalog when a db exists.
        self.catalog = catalog

    async def sync_catalog(self) -> object | None:
        """Reconcile the live registry into the persistent catalog (Part 2
        §36/§37). Called from Atlas.start() once the database and bus are up.
        Failure-isolated per source; never blocks startup."""
        if self.catalog is None:
            return None
        try:
            await self.catalog.initialize()
            summary: object | None = dict(self.catalog.summary())
            return summary
        except Exception as exc:
            _log.error(
                "tooling.catalog.sync_error",
                event_type="tooling",
                error=f"{type(exc).__name__}: {exc}",
            )
            return None

    async def initialize(self) -> InitializationReport:
        """Initialize and validate every registered tool with isolation."""
        ready: list[str] = []
        failed: list[str] = []
        skipped: list[str] = []
        details: dict[str, str] = {}

        for registration in self.registry.list_registrations():
            tool_id = registration.definition.id
            if registration.status.state == ToolRuntimeState.DISABLED:
                # Operator choice wins: disabled tools stay disabled, not ready.
                skipped.append(tool_id)
                continue
            try:
                await registration.adapter.initialize()
                await registration.adapter.validate(registration.definition)
                self.registry.set_status(tool_id, ToolRuntimeState.READY)
                ready.append(tool_id)
            except ToolingError as exc:
                detail = str(exc)
                self.registry.set_status(tool_id, ToolRuntimeState.FAILED, detail=detail)
                failed.append(tool_id)
                details[tool_id] = detail
                _log.error(
                    "tooling.initialize.failed",
                    event_type="tooling",
                    tool_id=tool_id,
                    error=detail,
                )
            except Exception as exc:
                detail = f"{type(exc).__name__}: {exc}"
                self.registry.set_status(tool_id, ToolRuntimeState.FAILED, detail=detail)
                failed.append(tool_id)
                details[tool_id] = detail
                _log.error(
                    "tooling.initialize.failed",
                    event_type="tooling",
                    tool_id=tool_id,
                    error=detail,
                )

        if failed:
            _log.warning(
                "tooling.initialize.partial",
                event_type="tooling",
                ready=len(ready),
                failed=len(failed),
            )
        else:
            _log.info(
                "tooling.initialize.complete",
                event_type="tooling",
                ready=len(ready),
                skipped=len(skipped),
            )
        return InitializationReport(
            ready=tuple(ready),
            failed=tuple(failed),
            skipped=tuple(skipped),
            failure_details=details,
        )

    async def shutdown(self) -> None:
        """Shut down each unique adapter instance once, isolating failures."""
        seen: set[int] = set()
        for registration in self.registry.list_registrations():
            adapter = registration.adapter
            if id(adapter) in seen:
                continue
            seen.add(id(adapter))
            try:
                await adapter.shutdown()
            except Exception as exc:
                _log.error(
                    "tooling.shutdown.error",
                    event_type="tooling",
                    adapter=adapter.kind,
                    error=f"{type(exc).__name__}: {exc}",
                )
