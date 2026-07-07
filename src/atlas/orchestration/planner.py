"""Planner — structured plans, zero execution.

WHY plan-then-act (not act-directly): a plan is inspectable, cacheable, and
gated. Risk/cost/confidence let the runtime escalate (cloud) or force
confirmation BEFORE any side effect. depends_on on steps means the SAME plan
shape supports linear now and parallel DAG later (Phase 12) with no rewrite.
"""

from __future__ import annotations

import json

from atlas.infra.ids import CorrelationId
from atlas.infra.logging import get_logger
from atlas.infra.types import ModelRequest, Tier
from atlas.intelligence.gateway import ModelGateway
from atlas.orchestration.errors import PlanningError
from atlas.orchestration.types import Capabilities, Plan, PlanStep, RiskLevel

_log = get_logger("atlas.orch.planner")

_PLAN_SYSTEM = (
    "You are a planner for an autonomous agent. Given CONTEXT and a REQUEST, "
    "produce a plan as ONLY JSON: {\"goal\":str,\"constraints\":[str],"
    "\"steps\":[{\"index\":int,\"intent\":str,\"tool\":str|null,"
    "\"operation\":str|null,\"args\":object,\"depends_on\":[int],"
    "\"expected_output\":str|null}],\"termination_conditions\":[str],"
    "\"risk\":\"low|medium|high\",\"estimated_cost_usd\":number,"
    "\"confidence\":number,\"unknowns\":[str]}. Prefer few, concrete steps."
)


class Planner:
    def __init__(self, gateway: ModelGateway) -> None:
        self._gw = gateway

    async def plan(
        self, request: str, context: str, caps: Capabilities, correlation_id: CorrelationId,
    ) -> Plan:
        resp = await self._gw.complete(ModelRequest(
            correlation_id=correlation_id, system=_PLAN_SYSTEM,
            prompt=f"CONTEXT:\n{context}\n\nREQUEST:\n{request}",
            needs_deep_reasoning=caps.needs_reasoning,
            stakes_tier=Tier.CONFIRM if caps.needs_confirmation else Tier.AUTO,
            max_tokens=1200,
        ))
        try:
            data = json.loads(self._json(resp.text))
            return self._to_plan(data)
        except (json.JSONDecodeError, ValueError, KeyError) as exc:
            raise PlanningError(f"could not parse plan: {exc}") from exc

    def _to_plan(self, data: dict[str, object]) -> Plan:
        raw_steps = data.get("steps")
        if not isinstance(raw_steps, list):
            raw_steps = []
        steps = tuple(
            PlanStep(
                index=int(s.get("index", 0)), intent=str(s.get("intent", "")),
                tool=s.get("tool") if isinstance(s.get("tool"), str) else None,
                operation=s.get("operation") if isinstance(s.get("operation"), str) else None,
                args=dict(s.get("args", {})) if isinstance(s.get("args"), dict) else {},
                depends_on=tuple(
                    int(d) for d in s.get("depends_on", []) if isinstance(d, (int, str, float))
                ),
                expected_output=s.get("expected_output")
                if isinstance(s.get("expected_output"), str)
                else None,
            )
            for s in raw_steps if isinstance(s, dict)
        )
        raw_constraints = data.get("constraints")
        if not isinstance(raw_constraints, list):
            raw_constraints = []
        raw_tc = data.get("termination_conditions")
        if not isinstance(raw_tc, list):
            raw_tc = []
        raw_unk = data.get("unknowns")
        if not isinstance(raw_unk, list):
            raw_unk = []
        raw_cost = data.get("estimated_cost_usd")
        raw_conf = data.get("confidence")
        return Plan(
            goal=str(data.get("goal", "")),
            constraints=tuple(str(c) for c in raw_constraints),
            steps=steps,
            termination_conditions=tuple(str(t) for t in raw_tc),
            risk=self._risk(data.get("risk", "low")),
            estimated_cost_usd=float(str(raw_cost)) if raw_cost is not None else 0.0,
            confidence=float(str(raw_conf)) if raw_conf is not None else 0.5,
            unknowns=tuple(str(u) for u in raw_unk),
        )

    @staticmethod
    def _risk(raw: object) -> RiskLevel:
        try:
            return RiskLevel(str(raw))
        except ValueError:
            return RiskLevel.MEDIUM

    @staticmethod
    def _json(text: str) -> str:
        s, e = text.find("{"), text.rfind("}")
        if s == -1 or e == -1:
            raise ValueError("no JSON")
        return text[s : e + 1]
