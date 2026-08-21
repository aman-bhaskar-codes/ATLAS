"""Six-level evaluation hierarchy (Prompt 4 §4-§5).

LEVEL 1 deterministic checks — used whenever they can answer the question.
LEVEL 2 programmatic verification (the runtime verifier's own verdict).
LEVEL 3 domain-specific evaluators (pluggable per task type).
LEVEL 4 Ragas-style metrics for knowledge/research dimensions.
LEVEL 5 LLM judge — ONLY through the ATLAS ModelGateway, ONLY for dimensions
        no lower level can score, and NEVER the same model that generated the
        trajectory (evaluator independence, §139).
LEVEL 6 human feedback.

"Do NOT use an LLM judge when deterministic verification can answer the
question reliably." — resolution is bottom-up: a dimension is scored at the
lowest level that can score it, and higher levels are never consulted for it.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from atlas.adaptation.domain import EvaluationVerdict, OutcomeEvaluation, TrajectoryEvaluation
from atlas.infra.clock import Clock, SystemClock
from atlas.infra.logging import get_logger
from atlas.memory.trajectory import Trajectory

_log = get_logger("atlas.adaptation.evaluators")


class EvaluatorLevel(IntEnum):
    DETERMINISTIC = 1
    PROGRAMMATIC = 2
    DOMAIN = 3
    RAGAS = 4
    LLM_JUDGE = 5
    HUMAN = 6


class DeterministicCheck(Protocol):
    """LEVEL 1: a check that can answer its dimension without any model."""

    name: str

    def check(self, trajectory: Trajectory) -> float | None:
        """Return a 0..1 score, or None when this check does not apply."""
        ...


class DomainEvaluator(Protocol):
    """LEVEL 3: a task-type-specific evaluator returning dimension scores."""

    def applies_to(self, trajectory: Trajectory) -> bool: ...

    async def evaluate(self, trajectory: Trajectory) -> dict[str, float]: ...


class JudgeGateway(Protocol):
    """Minimal gateway surface the LEVEL 5 judge needs — the real ModelGateway
    satisfies it; tests inject fakes. Adaptation never calls providers
    directly (§84)."""

    async def judge_score(self, *, prompt: str, generator_model: str | None) -> float | None: ...


class RagasInputs(BaseModel):
    """Optional knowledge/research inputs for LEVEL 4 scoring."""

    model_config = ConfigDict(frozen=True)

    answer: str = ""
    query: str = ""
    contexts: tuple[str, ...] = ()
    ground_truth: str = ""


class HierarchyResolution(BaseModel):
    """Which level scored which dimension — the audit trail of §5."""

    model_config = ConfigDict(frozen=True)

    dimension: str
    level: int
    score: float


class _SuccessCheck:
    """Goal achievement is deterministic: the task either succeeded or not."""

    name = "goal_achievement"

    def check(self, trajectory: Trajectory) -> float | None:
        return 1.0 if trajectory.success else 0.0


class _ErrorCheck:
    """Correctness lower bound: a completed run with no error and verification
    not contradicted scores 1.0; an errored run scores 0.0."""

    name = "correctness"

    def check(self, trajectory: Trajectory) -> float | None:
        if trajectory.error:
            return 0.0
        if trajectory.success:
            return 1.0
        return None


class _EfficiencyCheck:
    """Steps taken vs planned steps — deterministic, no model needed."""

    name = "efficiency"

    def check(self, trajectory: Trajectory) -> float | None:
        planned = max(len(trajectory.plan_steps), 1)
        if trajectory.steps_taken <= 0:
            return None
        ratio = planned / max(trajectory.steps_taken, planned)
        return round(min(1.0, ratio), 3)


class _ToolSelectionCheck:
    """Deterministic proxy: zero failed tool calls → good selection."""

    name = "tool_selection"

    def check(self, trajectory: Trajectory) -> float | None:
        if trajectory.tool_calls == 0:
            return None
        failed = sum(1 for o in trajectory.observations if not o.ok)
        return round(1.0 - failed / max(trajectory.tool_calls, 1), 3)


class EvaluationHierarchy:
    """Bottom-up dimension resolution across the six levels."""

    def __init__(
        self,
        *,
        judge: JudgeGateway | None = None,
        domain_evaluators: tuple[DomainEvaluator, ...] = (),
        deterministic_checks: tuple[DeterministicCheck, ...] | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._judge = judge
        self._domain_evaluators = domain_evaluators
        self._checks: tuple[DeterministicCheck, ...] = deterministic_checks or (
            _SuccessCheck(),
            _ErrorCheck(),
            _EfficiencyCheck(),
            _ToolSelectionCheck(),
        )
        self._clock = clock or SystemClock()

    async def evaluate(
        self,
        trajectory: Trajectory,
        *,
        ragas_inputs: RagasInputs | None = None,
        human_feedback: float | None = None,
        judge_dimensions: tuple[str, ...] = (),
    ) -> OutcomeEvaluation:
        """Score one trajectory. Dimensions are claimed by the lowest level
        able to score them; higher levels only see what is still unclaimed."""
        scores: dict[str, float] = {}
        levels: dict[str, int] = {}
        resolutions: list[HierarchyResolution] = []

        def claim(dimension: str, level: EvaluatorLevel, score: float) -> None:
            if dimension in scores:
                return  # already answered at a lower level — never override
            scores[dimension] = score
            levels[dimension] = int(level)
            resolutions.append(HierarchyResolution(dimension=dimension, level=int(level), score=score))

        # LEVEL 1 — deterministic
        for check in self._checks:
            score = check.check(trajectory)
            if score is not None:
                claim(check.name, EvaluatorLevel.DETERMINISTIC, score)

        # LEVEL 2 — programmatic verification (the runtime verifier itself)
        if trajectory.verification_passed is not None:
            claim(
                "verification",
                EvaluatorLevel.PROGRAMMATIC,
                trajectory.verification_score
                if trajectory.verification_score is not None
                else (1.0 if trajectory.verification_passed else 0.0),
            )

        # LEVEL 3 — domain evaluators
        for evaluator in self._domain_evaluators:
            if evaluator.applies_to(trajectory):
                for dimension, score in (await evaluator.evaluate(trajectory)).items():
                    claim(dimension, EvaluatorLevel.DOMAIN, score)

        # LEVEL 4 — Ragas-style knowledge/research metrics (pure functions)
        if ragas_inputs is not None and ragas_inputs.contexts:
            from atlas.evaluation.rag_metrics import answer_relevancy, context_precision

            claim(
                "knowledge_grounding",
                EvaluatorLevel.RAGAS,
                context_precision(ragas_inputs.query, list(ragas_inputs.contexts)),
            )
            if ragas_inputs.answer and ragas_inputs.query:
                claim(
                    "citation_quality",
                    EvaluatorLevel.RAGAS,
                    answer_relevancy(ragas_inputs.answer, ragas_inputs.query),
                )

        # LEVEL 5 — LLM judge, ONLY for dimensions no lower level claimed and
        # ONLY via the gateway, with evaluator independence (§139).
        unclaimed = [d for d in judge_dimensions if d not in scores]
        if unclaimed and self._judge is not None:
            for dimension in unclaimed:
                prompt = (
                    f"Score the '{dimension}' of this completed task from 0.0 to 1.0.\n"
                    f"Request: {trajectory.request}\nGoal: {trajectory.goal}\n"
                    f"Outcome: {'success' if trajectory.success else 'failure'}\n"
                    f"Answer: {(trajectory.answer or '')[:2000]}"
                )
                score = await self._judge.judge_score(prompt=prompt, generator_model=trajectory.model_version)
                if score is not None:
                    claim(dimension, EvaluatorLevel.LLM_JUDGE, score)

        # LEVEL 6 — human feedback
        if human_feedback is not None:
            claim("user_feedback", EvaluatorLevel.HUMAN, human_feedback)

        dimension_fields = {
            name: scores.get(name)
            for name in TrajectoryEvaluation.model_fields
            if name not in ("trajectory_id", "evaluator_levels", "created_ts")
        }
        dimensions = TrajectoryEvaluation(
            trajectory_id=trajectory.id,
            evaluator_levels=tuple(sorted(set(levels.values()))),
            created_ts=self._clock.now().isoformat(),
            **dimension_fields,
        )
        overall = sum(scores.values()) / len(scores) if scores else 0.0
        if trajectory.success and overall >= 0.5:
            verdict = EvaluationVerdict.PASS
        elif not trajectory.success:
            verdict = EvaluationVerdict.FAIL
        else:
            verdict = EvaluationVerdict.INCONCLUSIVE
        outcome = OutcomeEvaluation(
            trajectory_id=trajectory.id,
            verdict=verdict,
            overall_score=round(overall, 4),
            dimensions=dimensions,
            rationale="; ".join(f"{r.dimension}@L{r.level}={r.score:.2f}" for r in resolutions),
            created_ts=self._clock.now().isoformat(),
        )
        _log.info(
            "trajectory.evaluated",
            event_type="adaptation",
            trajectory_id=trajectory.id,
            verdict=verdict.value,
            overall=overall,
            dimensions=len(scores),
        )
        return outcome


__all__ = [
    "DeterministicCheck",
    "DomainEvaluator",
    "EvaluationHierarchy",
    "EvaluatorLevel",
    "HierarchyResolution",
    "JudgeGateway",
    "RagasInputs",
]
