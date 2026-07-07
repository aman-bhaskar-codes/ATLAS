"""Per-inference telemetry — OTel-shaped, audit-backed.

WHY it writes cost to the audit log: money has ONE ledger (Phase 1 audit), which
the cost governor reads. Every inference emits a structured record: provider,
model, tokens, cost, latency, attempts, fell_back, correlation/task ids. This is
the raw material for usage analytics + the benchmark store (5B).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from atlas.intelligence.contracts import InferenceRequest, ModelSpec, Usage

AuditCostHook = Callable[[str, str, str, Usage, int], Awaitable[None]]
# (correlation_id, provider, model_id, usage, latency_ms)


class Telemetry:
    def __init__(self, audit_cost: AuditCostHook) -> None:
        self._audit_cost = audit_cost

    async def record_success(self, req: InferenceRequest, spec: ModelSpec, usage: Usage, latency_ms: int) -> None:
        await self._audit_cost(str(req.correlation_id), spec.provider, spec.id, usage, latency_ms)

    async def record_failure(self, req: InferenceRequest, spec: ModelSpec, exc: Exception, latency_ms: int) -> None:
        await self._audit_cost(str(req.correlation_id), spec.provider, spec.id, Usage(), latency_ms)
