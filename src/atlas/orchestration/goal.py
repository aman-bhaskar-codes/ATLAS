"""Goal verification — checking work instead of trusting it (Phase 12).

WHY this module was rewritten: the previous implementation was a no-op in
production. ``GoalState`` was constructed in two places and neither populated
``success_criteria``, so the first branch of ``GoalVerifier.verify`` returned
``passed=True, score=1.0`` on every task without ever calling a model. The
verification-failure replan branch was therefore unreachable and
``TaskResult.verification_passed`` was a constant. It also caught bare
``Exception`` and returned ``passed=True``, making a crashed verifier
indistinguishable from a genuine pass.

Three things changed:
  1. ``GoalState``/``VerificationResult`` now come from ``atlas.infra.cognition``
     — one canonical definition, frozen, with criteria sourced from ``TaskIntent``.
  2. No criteria is reported as ``not_applicable``, not as a pass. A task that
     was never checked must not claim it was verified.
  3. A verifier error fails CLOSED.

WHY the Verifier protocol takes a correlation id: the old implementation hard
coded ``CorrelationId("verification")``, so verification model calls and their
cost could not be attributed to the task that caused them.
"""

from __future__ import annotations

import json
import time

from atlas.infra.cognition import (
    CriterionResult,
    Evidence,
    GoalState,
    TaskDomain,
    VerificationResult,
    Verifier,
)
from atlas.infra.ids import CorrelationId
from atlas.infra.logging import get_logger
from atlas.infra.types import ModelCapability, ModelRequest
from atlas.intelligence.gateway import ModelGateway
from atlas.orchestration.plan_parsing import extract_json_object

__all__ = [
    "CriterionResult",
    "Evidence",
    "GoalState",
    "GoalVerifier",
    "NullVerifier",
    "VerificationResult",
    "Verifier",
]

_log = get_logger("atlas.orchestration.goal")


class NullVerifier:
    """Declines to verify, and says so.

    WHY not ``passed=True, score=1.0``: that is a claim of verification. This
    returns ``not_applicable``, which records ``verifier="none"`` so a
    trajectory cannot later be mistaken for evidence that the work was checked.
    """

    name = "none"

    async def verify(
        self,
        goal: GoalState,
        answer: str,
        correlation_id: CorrelationId,
        context: str = "",
        domain: TaskDomain = TaskDomain.UNKNOWN,
        evidence: tuple[Evidence, ...] = (),
    ) -> VerificationResult:
        del goal, answer, correlation_id, context, domain, evidence
        return VerificationResult.not_applicable("verification disabled")


class GoalVerifier:
    """Evaluates a final answer against the goal's success criteria via model.

    This is the general-purpose verifier and the fallback for domains that have
    no executable check. Domains that *can* be checked mechanically (tests,
    filesystem state, source evidence) are handled by the capability-aware
    verifiers in ``atlas.orchestration.verification``, which prefer real
    evidence over a model opinion.
    """

    _SYSTEM = (
        "You are a strict evaluator. Given a GOAL, SUCCESS_CRITERIA and a FINAL_ANSWER, "
        "decide whether the answer satisfies each criterion. "
        "Output ONLY JSON: "
        '{"passed":bool,"score":0.0-1.0,'
        '"criteria_results":[{"criterion":str,"passed":bool,"detail":str}],'
        '"failure_reason":str|null,"suggested_next_action":str|null}. '
        "Judge only what the answer demonstrably establishes. "
        "An answer that asserts success without evidence does not satisfy a criterion. "
        "Score 1.0 means every criterion is fully satisfied."
    )

    name = "goal_criteria"

    def __init__(self, gateway: ModelGateway, *, min_pass_score: float = 0.5) -> None:
        self._gateway = gateway
        self._min_pass_score = max(0.0, min(1.0, min_pass_score))

    async def verify(
        self,
        goal: GoalState,
        answer: str,
        correlation_id: CorrelationId,
        context: str = "",
        domain: TaskDomain = TaskDomain.UNKNOWN,
        evidence: tuple[Evidence, ...] = (),
    ) -> VerificationResult:
        del context, domain
        if not goal.success_criteria:
            # Honest signal, not a pass. The understanding stage is responsible
            # for producing criteria; if it produced none, say so loudly.
            _log.info(
                "goal.verification_skipped",
                event_type="orchestration",
                correlation_id=correlation_id,
                detail="intent declared no success criteria",
            )
            return VerificationResult.not_applicable("no success criteria declared")

        started = time.perf_counter()
        criteria_str = "\n".join(f"- {c}" for c in goal.success_criteria)
        # WHY evidence is shown separately from the answer: the model must be
        # able to distinguish what the run actually observed from what the
        # answer merely claims. Without it, a confident assertion reads the
        # same as a demonstrated result.
        evidence_str = (
            "\n".join(
                f"- [{'ok' if e.ok else 'FAILED'}] {e.source}"
                f"{'.' + e.operation if e.operation else ''}: {e.summary[:300]}"
                for e in evidence[:12]
            )
            or "(none recorded)"
        )
        prompt = (
            f"GOAL:\n{goal.objective}\n\n"
            f"SUCCESS_CRITERIA:\n{criteria_str}\n\n"
            f"OBSERVED_EVIDENCE:\n{evidence_str}\n\n"
            f"FINAL_ANSWER:\n{answer[:2000]}"
        )

        try:
            resp = await self._gateway.complete(
                ModelRequest(
                    correlation_id=correlation_id,
                    system=self._SYSTEM,
                    prompt=prompt,
                    required_capabilities=frozenset(
                        {ModelCapability.REASONING, ModelCapability.JSON_GENERATION}
                    ),
                    max_tokens=512,
                    temperature=0.0,
                    # Phase 4: judging evidence is DEEP-tier work.
                    needs_deep_reasoning=True,
                )
            )
            data = json.loads(extract_json_object(str(resp.text)))
            if not isinstance(data, dict):
                raise ValueError("verification JSON is not an object")

            results = _criteria_results(data.get("criteria_results"), goal.success_criteria)
            score = _clamp(data.get("score"))
            passed = bool(data.get("passed", False))
            # Cross-check the model against itself: claiming pass while marking
            # a criterion unmet is incoherent, and we resolve it conservatively.
            if passed and results and any(not r.passed for r in results):
                passed = False
                score = min(score, 0.49)
            # A pass asserted below the configured floor is self-contradictory
            # too — the model rated its own confidence in the pass as low.
            if passed and score < self._min_pass_score:
                passed = False

            return VerificationResult(
                passed=passed,
                score=score,
                verifier=self.name,
                criteria_results=results,
                failure_reason=str(data["failure_reason"]) if data.get("failure_reason") else None,
                suggested_next_action=(
                    str(data["suggested_next_action"])
                    if data.get("suggested_next_action")
                    else None
                ),
                evidence=tuple(f"{e.source}: {e.summary[:120]}" for e in evidence[:8]),
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        except Exception as exc:
            # FAIL CLOSED. A broken verifier is not evidence of success.
            # The reasoning loop treats this as a verification miss, which is
            # bounded by max_replans, so a persistently broken verifier degrades
            # to "completed but unverified" rather than looping.
            _log.warning(
                "goal.verification_error",
                event_type="orchestration",
                correlation_id=correlation_id,
                error=repr(exc),
                detail="verifier failed; failing closed (passed=False)",
            )
            return VerificationResult.error(repr(exc), verifier=self.name)


def _criteria_results(raw: object, criteria: tuple[str, ...]) -> tuple[CriterionResult, ...]:
    """Parse criteria results, accepting both the list and legacy dict shapes."""
    if isinstance(raw, list):
        out = [
            CriterionResult(
                criterion=str(item.get("criterion", "")),
                passed=bool(item.get("passed", False)),
                detail=str(item.get("detail", "")),
            )
            for item in raw
            if isinstance(item, dict) and str(item.get("criterion", ""))
        ]
        if out:
            return tuple(out)
    if isinstance(raw, dict):
        return tuple(
            CriterionResult(criterion=str(k), passed=bool(v)) for k, v in raw.items() if str(k)
        )
    # No parseable per-criterion detail: report every declared criterion as
    # unmet rather than silently dropping them.
    return tuple(
        CriterionResult(criterion=c, passed=False, detail="no result reported") for c in criteria
    )


def _clamp(raw: object) -> float:
    try:
        return max(0.0, min(1.0, float(str(raw))))
    except (TypeError, ValueError):
        return 0.0
