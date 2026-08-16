"""Replanner — controlled dynamic plan revision after failures.

WHY bounded replanning: static plans fail when the environment surprises the
agent (missing file, API error, wrong assumption). Replanning without bounds
creates infinite loops. Every replan costs tokens and time — the counter
enforces the budget.

WHY separate from Planner: the Planner creates plans from scratch. The
Replanner creates revised plans from *failure context* — it has access to
what was tried and what failed, which changes the prompt significantly.

INVARIANT: replanning never exceeds GoalState.max_replans. The ReasoningLoop
checks GoalState.can_replan() before calling us.
"""

from __future__ import annotations

import json

from atlas.infra.ids import CorrelationId
from atlas.infra.logging import get_logger
from atlas.infra.types import ModelCapability, ModelRequest
from atlas.intelligence.gateway import ModelGateway
from atlas.orchestration.goal import GoalState, VerificationResult
from atlas.orchestration.plan_parsing import extract_json_object, plan_from_llm_json
from atlas.orchestration.types import Capabilities, Observation, Plan

_log = get_logger("atlas.orch.replanner")

_REPLAN_SYSTEM = (
    "You are a replanner for an autonomous agent. A previous plan failed or its "
    "verification did not pass. Given the GOAL, FAILURE_CONTEXT, and OBSERVATIONS, "
    "produce a revised plan as ONLY JSON: "
    '{"goal":str,"constraints":[str],"steps":[{"index":int,"intent":str,"tool":str|null,'
    '"operation":str|null,"args":object,"depends_on":[int],"expected_output":str|null}],'
    '"termination_conditions":[str],"risk":"low|medium|high","estimated_cost_usd":number,'
    '"confidence":number,"unknowns":[str]}. '
    "Address the specific failure. Do not repeat what already failed. Be concrete."
)


class Replanner:
    """Generates a revised plan after failure or verification miss.

    Used by ReasoningLoop when:
      1. All retries exhausted on a tool dispatch
      2. GoalVerifier returns passed=False

    The caller is responsible for checking GoalState.can_replan() before
    calling replan(). This class does not enforce that limit.
    """

    def __init__(self, gateway: ModelGateway) -> None:
        self._gw = gateway

    async def should_replan(
        self,
        goal: GoalState,
        last_obs: Observation,
        verification: VerificationResult | None = None,
    ) -> bool:
        """Decide whether replanning is warranted.

        Rule-based — no LLM call (must be fast).

        Triggers:
        - Tool dispatch failed (last_obs.ok is False) AND goal.can_replan()
        - Verification failed with score < 0.5 AND goal.can_replan()
        """
        if not goal.can_replan():
            return False
        if not last_obs.ok:
            _log.debug(
                "replanner.trigger_tool_failure",
                event_type="orchestration",
                error=last_obs.error,
            )
            return True
        if verification is not None and not verification.passed and verification.score < 0.5:
            _log.debug(
                "replanner.trigger_verification_failure",
                event_type="orchestration",
                score=verification.score,
            )
            return True
        return False

    async def replan(
        self,
        goal: GoalState,
        original_plan: Plan,
        failure_context: str,
        correlation_id: CorrelationId,
        caps: Capabilities | None = None,
    ) -> Plan:
        """Generate a revised plan given what failed.

        Args:
            goal:             Current goal state (objective + criteria).
            original_plan:    The plan that was being executed.
            failure_context:  Concise description of what went wrong.
            correlation_id:   For LLM request tracing.
            caps:             Optional capabilities hint (uses original plan's risk by default).

        Returns:
            A new Plan. The caller must update GoalState.replan_count.
        """
        prompt = (
            f"GOAL:\n{goal.to_prompt_fragment()}\n\n"
            f"ORIGINAL PLAN SUMMARY:\n"
            f"- {len(original_plan.steps)} steps, risk={original_plan.risk.value}, "
            f"confidence={original_plan.confidence:.2f}\n"
            f"- Goal: {original_plan.goal}\n\n"
            f"FAILURE CONTEXT:\n{failure_context}\n\n"
            f"REPLAN #{goal.replan_count + 1} of {goal.max_replans} allowed."
        )

        resp = await self._gw.complete(
            ModelRequest(
                correlation_id=correlation_id,
                system=_REPLAN_SYSTEM,
                prompt=prompt,
                required_capabilities=frozenset(
                    {
                        ModelCapability.PLANNING,
                        ModelCapability.REASONING,
                        ModelCapability.JSON_GENERATION,
                    }
                ),
                needs_deep_reasoning=True,  # replanning needs more thought
                max_tokens=2048,
            )
        )

        try:
            raw = resp.text
            s, e = raw.find("{"), raw.rfind("}")
            if s == -1 or e == -1:
                raise ValueError("no JSON in replanner response")
            data = json.loads(extract_json_object(raw))
            return plan_from_llm_json(data)
        except Exception as exc:
            _log.warning(
                "replanner.parse_failed",
                event_type="orchestration",
                error=str(exc),
                replan_count=goal.replan_count,
            )
            # Return original plan so the loop can continue (it will eventually
            # hit max_steps or max_replans and terminate gracefully)
            return original_plan
