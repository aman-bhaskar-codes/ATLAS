"""Per-capability-execution telemetry — OTel-shaped, audit-backed.

WHY audit-backed: capability executions (esp. cost-bearing ones like search) must
land in the same single ledger as model calls, so usage analytics + budget see one
truth. Records capability, provider, ok, latency, correlation/task ids.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from atlas.capabilities.registry.capability import Capability

AuditHook = Callable[..., Awaitable[None]]


class CapabilityTelemetry:
    def __init__(self, audit_hook: AuditHook) -> None:
        self._audit = audit_hook

    async def record(
        self, *, correlation_id: str, capability: Capability, provider: str,
        ok: bool, latency_ms: int, task_id: str | None = None,
    ) -> None:
        await self._audit(
            correlation_id=correlation_id, actor="capability",
            action=f"capability.{capability.value}", tool=provider,
            outcome="ok" if ok else "error",
            payload={"latency_ms": latency_ms, "task_id": task_id},
        )
