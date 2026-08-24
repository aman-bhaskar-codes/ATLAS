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
from atlas.infra.types import ModelCapability, ModelRequest, Tier
from atlas.intelligence.gateway import ModelGateway
from atlas.orchestration.errors import PlanningError
from atlas.orchestration.plan_parsing import extract_json_object, plan_from_llm_json
from atlas.orchestration.types import Capabilities, Plan

_log = get_logger("atlas.orch.planner")

_PLAN_SYSTEM = (
    "You are a planner for an autonomous agent. Given CONTEXT and a REQUEST, "
    'produce a plan as ONLY JSON: {"goal":str,"constraints":[str],'
    '"steps":[{"index":int,"intent":str,"tool":str|null,'
    '"operation":str|null,"args":object,"depends_on":[int],'
    '"expected_output":str|null}],"termination_conditions":[str],'
    '"risk":"low|medium|high","estimated_cost_usd":number,'
    '"confidence":number,"unknowns":[str]}. Prefer few, concrete steps.'
)


class Planner:
    def __init__(self, gateway: ModelGateway) -> None:
        self._gw = gateway

    async def plan(
        self,
        request: str,
        context: str,
        caps: Capabilities,
        correlation_id: CorrelationId,
        prior_knowledge: str = "",
    ) -> Plan:
        """Generate a plan.

        prior_knowledge: lessons/skills/strategies retrieved from memory so the
        planner builds on proven approaches instead of rediscovering them.
        Advisory only — retrieved knowledge never relaxes constraints.
        """
        knowledge_block = f"\n\nPRIOR KNOWLEDGE (advisory):\n{prior_knowledge}\n" if prior_knowledge else ""
        resp = await self._gw.complete(
            ModelRequest(
                correlation_id=correlation_id,
                system=_PLAN_SYSTEM,
                prompt=f"CONTEXT:\n{context}{knowledge_block}\n\nREQUEST:\n{request}",
                required_capabilities=frozenset(
                    {
                        ModelCapability.PLANNING,
                        ModelCapability.JSON_GENERATION,
                    }
                ),
                needs_deep_reasoning=caps.needs_reasoning,
                stakes_tier=Tier.CONFIRM if caps.needs_confirmation else Tier.AUTO,
                # WHY 6144: qwen3:4b writes verbose chain-of-thought BEFORE the JSON.
                # 2048 runs out mid-thinking, so no JSON is ever output.
                max_tokens=6144,
            )
        )
        try:
            data = json.loads(extract_json_object(resp.text))
            return plan_from_llm_json(data)
        except (json.JSONDecodeError, ValueError, KeyError) as exc:
            _log.warning(
                "planning.parse_failed",
                event_type="orch",
                correlation_id=str(correlation_id),
                error=repr(exc),
                raw_text_len=len(resp.text),
            )
            raise PlanningError(f"could not parse plan: {exc}. Raw text: {resp.text[:500]}") from exc
