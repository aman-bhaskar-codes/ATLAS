"""Safety Engine — the reference monitor funnel.

INVARIANT: nothing executes a tool except through guard(). The pipeline is:
kill-switch check -> classify -> policy chain -> AUDIT the decision -> branch.
The kill switch is re-checked AFTER any confirmation wait, because a human
confirmation can take minutes and the world may have changed. Every failure
path is audited before the exception is raised.
"""

from __future__ import annotations

import asyncio
import time

from atlas.infra.clock import Clock
from atlas.infra.config import SafetyCfg
from atlas.infra.errors import AtlasError
from atlas.infra.logging import get_logger
from atlas.infra.types import AuditRecord, SafetyDecision, ToolRequest, ToolResult
from atlas.safety.audit import AuditLog
from atlas.safety.classifier import TierClassifier
from atlas.safety.confirm import Confirmer
from atlas.safety.killswitch import KillSwitch
from atlas.safety.policy import PolicyEngine
from atlas.tools.base import Tool

_log = get_logger("atlas.safety.engine")


class HaltedError(AtlasError):
    """Raised when the kill switch stops an action."""


class DeniedError(AtlasError):
    def __init__(self, decision: SafetyDecision) -> None:
        super().__init__(decision.reason)
        self.decision = decision


class SafetyEngine:
    def __init__(
        self, *, classifier: TierClassifier, policy: PolicyEngine,
        audit: AuditLog, killswitch: KillSwitch, clock: Clock, cfg: SafetyCfg,
        confirmer: Confirmer | None = None,
    ) -> None:
        self._clf = classifier
        self._policy = policy
        self._audit = audit
        self._ks = killswitch
        self._clock = clock
        self._cfg = cfg
        self._confirmer = confirmer

    def set_confirmer(self, confirmer: Confirmer) -> None:
        self._confirmer = confirmer

    async def guard(self, req: ToolRequest, tool: Tool) -> ToolResult:
        if self._ks.is_active():
            await self._audit_decision(req, None, outcome="halted")
            raise HaltedError("kill switch active")

        decision = self._policy.evaluate(self._clf.classify(req))
        await self._audit_decision(req, decision)

        if decision.decision == "deny":
            raise DeniedError(decision)

        if decision.decision == "require_confirm":
            if not await self._confirm(req, decision, tool):
                await self._audit_decision(req, decision, outcome="denied")
                raise DeniedError(decision)

        if self._ks.is_active():  # re-check: confirmation may have taken time
            await self._audit_decision(req, decision, outcome="halted")
            raise HaltedError("kill switch active (post-confirm)")

        return await self._execute(req, decision, tool)

    async def _execute(self, req: ToolRequest, decision: SafetyDecision, tool: Tool) -> ToolResult:
        start = time.perf_counter()
        try:
            result = await tool.execute(req.args)
        except Exception as exc:
            await self._audit_decision(req, decision, outcome="error")
            _log.error("tool.execute_failed", event_type="safety",
                       correlation_id=req.correlation_id, tool=req.tool, error=repr(exc))
            raise
        dur = int((time.perf_counter() - start) * 1000)
        await self._audit.record(AuditRecord(
            correlation_id=req.correlation_id, ts=self._clock.now(), actor="safety",
            action="tool.result", tool=req.tool, tier=decision.tier, decision=decision.decision,
            outcome="ok" if result.ok else "error",
            payload={"duration_ms": dur, "error": result.error,
                     "side_effects": [se.model_dump() for se in result.side_effects]},
        ))
        return result

    async def _confirm(self, req: ToolRequest, decision: SafetyDecision, tool: Tool) -> bool:
        if self._confirmer is None:
            _log.warning("safety.no_confirmer", event_type="safety",
                         correlation_id=req.correlation_id)
            return False
        preview = tool.dry_run(req.args)
        prompt = (
            f"[TIER {int(decision.tier)}] {req.tool}.{req.operation}\n"
            f"reason: {decision.reason}\nwould do: {preview}"
        )
        try:
            return await asyncio.wait_for(
                self._confirmer.confirm(prompt, decision, req),
                timeout=self._cfg.confirm_timeout_s,
            )
        except TimeoutError:
            _log.info("safety.confirm_timeout", event_type="safety",
                      correlation_id=req.correlation_id)
            return False

    async def _audit_decision(
        self, req: ToolRequest, decision: SafetyDecision | None, outcome: str | None = None
    ) -> None:
        await self._audit.record(AuditRecord(
            correlation_id=req.correlation_id, ts=self._clock.now(), actor="safety",
            action="decision", tool=req.tool,
            tier=decision.tier if decision else None,
            decision=decision.decision if decision else None,
            outcome=outcome,
            payload={"operation": req.operation, "args": req.args,
                     "reason": decision.reason if decision else "halted",
                     "matched_rule": decision.matched_rule if decision else None},
        ))
