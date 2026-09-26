"""ToolingRegistry — one registry for every tool source.

WHY a dict keyed by stable ID, dependency-injected (Part 1 §18/§81): the
registry is constructed in ``bootstrap/tooling.py`` and handed to consumers —
no global singleton. Definitions are immutable; the only mutable state is the
per-tool ``ToolStatus`` (enabled/ready), kept deliberately separate (§42).

Part 2's catalog and Part 3's router build on ``list_registrations``/``find`` —
the structured enumeration is already the catalog-friendly interface (§21).
"""

from __future__ import annotations

from dataclasses import dataclass

from atlas.tooling.adapters.base import ToolAdapter
from atlas.tooling.errors import ToolAlreadyRegistered, ToolNotFound
from atlas.tooling.models.identity import ToolNamespace
from atlas.tooling.models.tool_definition import ExecutionType, UniversalToolDefinition
from atlas.tooling.models.tool_health import ToolRuntimeState, ToolStatus


@dataclass
class ToolRegistration:
    """A definition + its adapter + its runtime status. Metadata and execution
    stay separate (§19): the definition never executes, the adapter does."""

    definition: UniversalToolDefinition
    adapter: ToolAdapter
    status: ToolStatus


class ToolingRegistry:
    def __init__(self) -> None:
        self._registrations: dict[str, ToolRegistration] = {}

    # ── Registration ──────────────────────────────────────────────── #

    def register(
        self,
        definition: UniversalToolDefinition,
        adapter: ToolAdapter,
        *,
        status: ToolStatus | None = None,
    ) -> None:
        """Register one tool. Duplicate identity is a typed error (§17)."""
        if definition.id in self._registrations:
            raise ToolAlreadyRegistered(
                f"tool {definition.id!r} is already registered (unregister it first to replace it)"
            )
        self._registrations[definition.id] = ToolRegistration(
            definition=definition,
            adapter=adapter,
            status=status or ToolStatus(),
        )

    def unregister(self, tool_id: str) -> ToolRegistration:
        if tool_id not in self._registrations:
            raise ToolNotFound(f"tool {tool_id!r} is not registered")
        return self._registrations.pop(tool_id)

    def update(self, definition: UniversalToolDefinition) -> None:
        """Replace a definition in place (e.g. re-discovery), keeping status."""
        registration = self._registrations.get(definition.id)
        if registration is None:
            raise ToolNotFound(f"tool {definition.id!r} is not registered")
        self._registrations[definition.id] = ToolRegistration(
            definition=definition,
            adapter=registration.adapter,
            status=registration.status,
        )

    # ── Lookup ────────────────────────────────────────────────────── #

    def get(self, tool_id: str) -> ToolRegistration | None:
        return self._registrations.get(tool_id)

    def require(self, tool_id: str) -> ToolRegistration:
        registration = self._registrations.get(tool_id)
        if registration is None:
            raise ToolNotFound(f"tool {tool_id!r} is not registered")
        return registration

    def list_definitions(self) -> tuple[UniversalToolDefinition, ...]:
        return tuple(r.definition for r in self._registrations.values())

    def list_registrations(self) -> tuple[ToolRegistration, ...]:
        return tuple(self._registrations.values())

    def __len__(self) -> int:
        return len(self._registrations)

    # ── Structured enumeration (catalog-friendly, §21) ────────────── #

    def find(
        self,
        *,
        namespace: ToolNamespace | str | None = None,
        execution_type: ExecutionType | str | None = None,
        capability: str | None = None,
        enabled: bool | None = None,
        tag: str | None = None,
    ) -> tuple[ToolRegistration, ...]:
        def matches(r: ToolRegistration) -> bool:
            if namespace is not None and r.definition.namespace != (
                namespace if isinstance(namespace, ToolNamespace) else ToolNamespace(namespace)
            ):
                return False
            if execution_type is not None and r.definition.execution_type != (
                execution_type if isinstance(execution_type, ExecutionType) else ExecutionType(execution_type)
            ):
                return False
            if capability is not None and r.definition.capability != capability:
                return False
            if enabled is not None:
                is_disabled = r.status.state == ToolRuntimeState.DISABLED
                if enabled == is_disabled:
                    return False
            if tag is not None and tag not in r.definition.tags:
                return False
            return True

        return tuple(r for r in self._registrations.values() if matches(r))

    def find_by_capability(self, capability: str) -> tuple[ToolRegistration, ...]:
        return tuple(r for r in self._registrations.values() if r.definition.capability == capability)

    def find_by_namespace(self, namespace: ToolNamespace | str) -> tuple[ToolRegistration, ...]:
        ns = namespace if isinstance(namespace, ToolNamespace) else ToolNamespace(namespace)
        return tuple(r for r in self._registrations.values() if r.definition.namespace == ns)

    def find_by_execution_type(self, execution_type: ExecutionType | str) -> tuple[ToolRegistration, ...]:
        et = execution_type if isinstance(execution_type, ExecutionType) else ExecutionType(execution_type)
        return tuple(r for r in self._registrations.values() if r.definition.execution_type == et)

    # ── Runtime state (§20/§57) ───────────────────────────────────── #

    def enable(self, tool_id: str) -> None:
        registration = self.require(tool_id)
        if registration.status.state == ToolRuntimeState.FAILED:
            # enabling a failed tool re-arms it for the next initialize pass
            self._registrations[tool_id] = ToolRegistration(
                definition=registration.definition,
                adapter=registration.adapter,
                status=ToolStatus(state=ToolRuntimeState.REGISTERED, detail="re-armed after failure"),
            )
            return
        self._registrations[tool_id] = ToolRegistration(
            definition=registration.definition,
            adapter=registration.adapter,
            status=ToolStatus(state=ToolRuntimeState.READY, detail=registration.status.detail),
        )

    def disable(self, tool_id: str) -> None:
        registration = self.require(tool_id)
        self._registrations[tool_id] = ToolRegistration(
            definition=registration.definition,
            adapter=registration.adapter,
            status=ToolStatus(
                state=ToolRuntimeState.DISABLED,
                detail=registration.status.detail or "disabled by operator",
            ),
        )

    def is_enabled(self, tool_id: str) -> bool:
        return self.require(tool_id).status.state != ToolRuntimeState.DISABLED

    def set_status(
        self,
        tool_id: str,
        state: ToolRuntimeState,
        detail: str | None = None,
    ) -> None:
        """Lifecycle transitions (initialize/shutdown) record state here.
        ``detail=None`` preserves the existing detail string."""
        registration = self.require(tool_id)
        self._registrations[tool_id] = ToolRegistration(
            definition=registration.definition,
            adapter=registration.adapter,
            status=ToolStatus(
                state=state,
                detail=registration.status.detail if detail is None else detail,
            ),
        )
