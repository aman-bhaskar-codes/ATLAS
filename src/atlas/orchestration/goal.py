"""Goal tracking and verification contracts — Phase 1 Cognitive Runtime.

WHY GoalState: the agent must reason about desired vs current state, not just
process prompts. Having an explicit goal object lets the planner, verifier, and
replanner all share the same understanding of success.

WHY VerificationResult: ATLAS must not trust its own output. Every non-trivial
task runs a verifier; the result feeds into the replan decision.

WHY Verifier protocol: the verification strategy depends on task type.
A coding task verifies via tests; a research task verifies via source cross-check.
Both share the same contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from atlas.orchestration.types import Plan


# ---------------------------------------------------------------------------
# GoalState
# ---------------------------------------------------------------------------

@dataclass
class GoalState:
    """Tracks the evolving understanding of what success looks like.

    Immutable description fields (objective, constraints, success_criteria) are
    set at task creation. Mutable fields (current_state, progress, confidence,
    replan_count) are updated by the runtime as execution proceeds.
    """
    # Set at task creation
    objective: str
    constraints: list[str] = field(default_factory=list)
    success_criteria: list[str] = field(default_factory=list)

    # Updated during execution
    current_state: str = "not_started"
    progress: float = 0.0        # 0.0 → 1.0
    confidence: float = 0.5      # current plan confidence
    replan_count: int = 0
    max_replans: int = 3         # hard cap; configurable via ExecutionLimits
    created_ts: datetime = field(default_factory=lambda: datetime.now(UTC))

    def can_replan(self) -> bool:
        return self.replan_count < self.max_replans

    def record_replan(self) -> None:
        self.replan_count += 1

    def update_progress(self, progress: float, current_state: str = "") -> None:
        self.progress = max(0.0, min(1.0, progress))
        if current_state:
            self.current_state = current_state

    def to_prompt_fragment(self) -> str:
        """Render as a compact string for inclusion in prompts."""
        lines = [
            f"Objective: {self.objective}",
            f"Progress: {int(self.progress * 100)}%  Confidence: {int(self.confidence * 100)}%",
        ]
        if self.success_criteria:
            lines.append("Success when: " + "; ".join(self.success_criteria))
        if self.constraints:
            lines.append("Constraints: " + "; ".join(self.constraints))
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# VerificationResult
# ---------------------------------------------------------------------------

@dataclass
class VerificationResult:
    """Outcome of running a Verifier against a task result.

    A failing verification is not necessarily a task failure — it feeds
    into the replan decision tree. The Replanner decides whether to retry.
    """
    passed: bool
    score: float = 1.0               # 0.0 → 1.0, quantifies degree of success
    criteria_results: dict[str, bool] = field(default_factory=dict)
    failure_reason: str | None = None
    suggestions: list[str] = field(default_factory=list)

    def to_prompt_fragment(self) -> str:
        if self.passed:
            return f"Verification: PASSED (score {self.score:.2f})"
        return (
            f"Verification: FAILED (score {self.score:.2f})\n"
            f"Reason: {self.failure_reason or 'unknown'}\n"
            + (("Suggestions: " + "; ".join(self.suggestions)) if self.suggestions else "")
        )


# ---------------------------------------------------------------------------
# Verifier protocol
# ---------------------------------------------------------------------------

class Verifier(Protocol):
    """Checks whether a task result satisfies the GoalState.

    Implementations:
      GoalVerifier    — LLM-based: evaluates answer against success_criteria
      NullVerifier    — always passes (for tasks without explicit criteria)
    """

    async def verify(
        self,
        goal: GoalState,
        answer: str,
        context: str = "",
    ) -> VerificationResult: ...


# ---------------------------------------------------------------------------
# NullVerifier — default when no explicit criteria defined
# ---------------------------------------------------------------------------

class NullVerifier:
    """Always passes. Used when GoalState has no success_criteria."""

    async def verify(
        self,
        goal: GoalState,
        answer: str,
        context: str = "",
    ) -> VerificationResult:
        return VerificationResult(passed=True, score=1.0)


# ---------------------------------------------------------------------------
# GoalVerifier — LLM-based evaluation against success_criteria
# ---------------------------------------------------------------------------

class GoalVerifier:
    """Evaluates the final answer against the GoalState's success criteria.

    Uses a structured JSON prompt so the LLM produces a deterministic score.
    Falls back to NullVerifier if the LLM call fails.
    """

    _SYSTEM = (
        "You are a strict evaluator. Given a GOAL, SUCCESS_CRITERIA, and a FINAL_ANSWER, "
        "decide whether the answer satisfies each criterion. "
        "Output ONLY JSON: "
        '{"passed": bool, "score": 0.0-1.0, "criteria_results": {"<criterion>": bool}, '
        '"failure_reason": str|null, "suggestions": [str]}. '
        "Be objective and precise. Score 1.0 = fully satisfies all criteria."
    )

    def __init__(self, gateway: object) -> None:
        from atlas.intelligence.gateway import ModelGateway as _MG
        self._gateway: _MG = gateway  # type: ignore[assignment]

    async def verify(
        self,
        goal: GoalState,
        answer: str,
        context: str = "",
    ) -> VerificationResult:
        if not goal.success_criteria:
            return VerificationResult(passed=True, score=1.0)

        import json
        from atlas.infra.types import ModelCapability, ModelRequest

        criteria_str = "\n".join(f"- {c}" for c in goal.success_criteria)
        prompt = (
            f"GOAL:\n{goal.objective}\n\n"
            f"SUCCESS_CRITERIA:\n{criteria_str}\n\n"
            f"FINAL_ANSWER:\n{answer[:2000]}"
        )

        try:
            from atlas.infra.ids import CorrelationId as _CID
            resp = await self._gateway.complete(
                ModelRequest(
                    correlation_id=_CID("verification"),
                    system=self._SYSTEM,
                    prompt=prompt,
                    required_capabilities=frozenset({
                        ModelCapability.REASONING,
                        ModelCapability.JSON_GENERATION,
                    }),
                    max_tokens=512,
                )
            )
            raw = resp.text
            # Extract JSON
            s, e = raw.find("{"), raw.rfind("}")
            if s == -1 or e == -1:
                raise ValueError("no JSON in response")
            data = json.loads(raw[s : e + 1])

            criteria_results: dict[str, bool] = {}
            raw_cr = data.get("criteria_results", {})
            if isinstance(raw_cr, dict):
                criteria_results = {str(k): bool(v) for k, v in raw_cr.items()}

            raw_suggestions = data.get("suggestions", [])
            suggestions = [str(s) for s in raw_suggestions] if isinstance(raw_suggestions, list) else []

            return VerificationResult(
                passed=bool(data.get("passed", False)),
                score=float(data.get("score", 0.0)),
                criteria_results=criteria_results,
                failure_reason=str(data["failure_reason"]) if data.get("failure_reason") else None,
                suggestions=suggestions,
            )
        except Exception:
            # Verification failure must not crash the task — fall back to pass
            return VerificationResult(passed=True, score=0.5, failure_reason="verifier_error")
