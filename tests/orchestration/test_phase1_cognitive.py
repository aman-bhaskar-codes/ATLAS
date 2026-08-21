"""Cognitive runtime contract tests — GoalState, Replanner, Verifier, TaskResult.

These exercise the Phase 1/12 contracts without real LLM calls.

WHY this file was rewritten: it previously asserted the *broken* behaviour as
if it were correct — ``test_goal_verifier_fallback_on_bad_json`` asserted
``passed is True`` for a crashed verifier, and ``test_null_verifier_always_passes``
asserted ``score == 1.0`` for work that was never checked. Those assertions
were the reason the fail-open defect survived: the suite was green *because*
it encoded the bug. The tests below assert that verification now fails closed.
"""

from __future__ import annotations

from typing import Any

import pytest

from atlas.orchestration.goal import (
    GoalState,
    GoalVerifier,
    NullVerifier,
    VerificationResult,
)
from atlas.orchestration.replanner import Replanner
from atlas.orchestration.types import (
    Observation,
    Plan,
    PlanStep,
    RiskLevel,
    TaskResult,
)

_CORR: Any = "test-corr"

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeGateway:
    """Fake ModelGateway that returns scripted responses."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self._calls: list[Any] = []

    async def complete(self, req: Any) -> Any:
        self._calls.append(req)
        text = self._responses.pop(0) if self._responses else '{"passed":false,"score":0.0}'

        class _Resp:
            cost = type("c", (), {"input_tokens": 10, "output_tokens": 5})()

        _Resp.text = text  # type: ignore[attr-defined]
        return _Resp()


def _verifier(responses: list[str], *, min_pass_score: float = 0.5) -> GoalVerifier:
    return GoalVerifier(_FakeGateway(responses), min_pass_score=min_pass_score)  # type: ignore[arg-type]


def _plan(goal: str = "do thing", confidence: float = 0.8) -> Plan:
    return Plan(
        goal=goal,
        steps=(PlanStep(index=0, intent="step one"),),
        risk=RiskLevel.LOW,
        confidence=confidence,
    )


def _obs(ok: bool, error: str | None = None, content: Any = None) -> Observation:
    return Observation(step=1, ok=ok, error=error, content=content or "result")


# ---------------------------------------------------------------------------
# GoalState — frozen, copy-on-transition
# ---------------------------------------------------------------------------


def test_goal_state_can_replan_within_budget() -> None:
    goal = GoalState(objective="test", max_replans=3)
    assert goal.can_replan() is True
    goal = goal.with_replan().with_replan().with_replan()
    assert goal.replan_count == 3
    assert goal.can_replan() is False


def test_goal_state_transitions_do_not_mutate_the_original() -> None:
    """The old mutable GoalState was shared by reference between the
    orchestrator and the reasoning loop, so neither could tell who had
    advanced it. Transitions must return copies."""
    original = GoalState(objective="test", max_replans=3)
    advanced = original.with_replan()
    assert original.replan_count == 0
    assert advanced.replan_count == 1
    assert advanced is not original


def test_goal_state_from_intent_carries_success_criteria() -> None:
    """The P0: criteria must survive the trip from intent to verifier."""
    from atlas.infra.cognition import TaskIntent

    intent = TaskIntent(
        objective="Fix the failing test",
        success_criteria=("pytest exits 0", "no new lint errors"),
        constraints=("do not touch src/atlas/safety",),
        confidence=0.7,
    )
    goal = GoalState.from_intent(intent)
    assert goal.success_criteria == ("pytest exits 0", "no new lint errors")
    assert goal.constraints == ("do not touch src/atlas/safety",)
    assert goal.confidence == pytest.approx(0.7, abs=0.01)


def test_goal_state_to_prompt_fragment() -> None:
    goal = GoalState(
        objective="Build a widget",
        constraints=("no cloud", "under $1"),
        success_criteria=("tests pass", "linter clean"),
    )
    fragment = goal.to_prompt_fragment()
    assert "Build a widget" in fragment
    assert "tests pass" in fragment
    assert "no cloud" in fragment


def test_goal_state_with_progress_clamps() -> None:
    goal = GoalState(objective="x").with_progress(0.5, "halfway")
    assert goal.progress == 0.5
    assert goal.current_state == "halfway"
    assert goal.with_progress(1.5).progress == 1.0
    assert goal.with_progress(-0.1).progress == 0.0


# ---------------------------------------------------------------------------
# NullVerifier — declines to verify, and says so
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_null_verifier_reports_not_applicable_not_a_pass() -> None:
    """score must be 0.0 and verifier must be "none": a task that was never
    checked must not be recordable as evidence that it was checked."""
    result = await NullVerifier().verify(
        GoalState(objective="anything", success_criteria=("a", "b")), "answer", _CORR
    )
    assert result.verifier == "none"
    assert result.score == 0.0
    assert result.failure_reason == "verification disabled"


# ---------------------------------------------------------------------------
# GoalVerifier
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_goal_verifier_parses_passed_response() -> None:
    v = _verifier(
        [
            '{"passed":true,"score":0.9,'
            '"criteria_results":[{"criterion":"tests pass","passed":true,"detail":"10/10"}],'
            '"failure_reason":null,"suggested_next_action":null}'
        ]
    )
    goal = GoalState(objective="test", success_criteria=("tests pass",))
    result = await v.verify(goal, "all 10 tests passed", _CORR)
    assert result.passed is True
    assert result.score == pytest.approx(0.9, abs=0.01)
    assert result.verifier == "goal_criteria"
    assert result.criteria_results[0].criterion == "tests pass"
    assert result.criteria_results[0].passed is True


@pytest.mark.asyncio
async def test_goal_verifier_parses_failed_response() -> None:
    v = _verifier(
        [
            '{"passed":false,"score":0.2,'
            '"criteria_results":[{"criterion":"tests pass","passed":false,"detail":"3 fail"}],'
            '"failure_reason":"Tests are still failing",'
            '"suggested_next_action":"Fix test_foo"}'
        ]
    )
    goal = GoalState(objective="test", success_criteria=("tests pass",))
    result = await v.verify(goal, "I tried but tests still fail", _CORR)
    assert result.passed is False
    assert result.score == pytest.approx(0.2, abs=0.01)
    assert result.failure_reason == "Tests are still failing"
    assert result.suggested_next_action == "Fix test_foo"


@pytest.mark.asyncio
async def test_goal_verifier_fails_closed_on_bad_json() -> None:
    """A crashed verifier is not evidence of success."""
    v = _verifier(["not json at all"])
    goal = GoalState(objective="test", success_criteria=("pass",))
    result = await v.verify(goal, "something", _CORR)
    assert result.passed is False
    assert result.score == 0.0
    assert result.failure_reason is not None
    assert result.failure_reason.startswith("verifier_error")
    assert result.suggested_next_action == "retry_verification"


@pytest.mark.asyncio
async def test_goal_verifier_rejects_incoherent_pass() -> None:
    """passed=true while a criterion is unmet is self-contradictory."""
    v = _verifier(
        [
            '{"passed":true,"score":0.95,'
            '"criteria_results":[{"criterion":"tests pass","passed":false,"detail":"3 fail"}]}'
        ]
    )
    goal = GoalState(objective="test", success_criteria=("tests pass",))
    result = await v.verify(goal, "done!", _CORR)
    assert result.passed is False
    assert result.score <= 0.49


@pytest.mark.asyncio
async def test_goal_verifier_rejects_pass_below_score_floor() -> None:
    v = _verifier(['{"passed":true,"score":0.3,"criteria_results":[]}'], min_pass_score=0.5)
    goal = GoalState(objective="test", success_criteria=("tests pass",))
    result = await v.verify(goal, "maybe done", _CORR)
    assert result.passed is False


@pytest.mark.asyncio
async def test_goal_verifier_unreported_criteria_default_to_unmet() -> None:
    """Silently dropping a criterion would let a partial answer pass."""
    v = _verifier(['{"passed":false,"score":0.4,"criteria_results":[]}'])
    goal = GoalState(objective="test", success_criteria=("a", "b"))
    result = await v.verify(goal, "answer", _CORR)
    assert len(result.criteria_results) == 2
    assert all(not c.passed for c in result.criteria_results)


@pytest.mark.asyncio
async def test_goal_verifier_no_criteria_is_not_applicable_not_a_pass() -> None:
    gw = _FakeGateway([])  # no responses — must not call the gateway
    v = GoalVerifier(gw)  # type: ignore[arg-type]
    result = await v.verify(GoalState(objective="test"), "anything", _CORR)
    assert result.verifier == "none"
    assert result.score == 0.0
    assert result.failure_reason == "no success criteria declared"
    assert len(gw._calls) == 0, "must not call gateway when no criteria"


@pytest.mark.asyncio
async def test_goal_verifier_attributes_calls_to_the_real_correlation_id() -> None:
    """The old implementation hard-coded CorrelationId("verification"), so
    verification cost could not be joined back to the task that caused it."""
    gw = _FakeGateway(['{"passed":true,"score":1.0,"criteria_results":[]}'])
    v = GoalVerifier(gw)  # type: ignore[arg-type]
    goal = GoalState(objective="test", success_criteria=("x",))
    await v.verify(goal, "answer", "corr-abc-123")  # type: ignore[arg-type]
    assert gw._calls[0].correlation_id == "corr-abc-123"


# ---------------------------------------------------------------------------
# VerificationResult
# ---------------------------------------------------------------------------


def test_verification_result_to_prompt_fragment_pass() -> None:
    r = VerificationResult(passed=True, score=0.95, verifier="goal_criteria")
    fragment = r.to_prompt_fragment()
    assert "PASSED" in fragment
    assert "0.95" in fragment


def test_verification_result_to_prompt_fragment_fail() -> None:
    from atlas.infra.cognition import CriterionResult

    r = VerificationResult(
        passed=False,
        score=0.3,
        verifier="goal_criteria",
        criteria_results=(CriterionResult(criterion="handles None", passed=False),),
        failure_reason="Missing edge case",
        suggested_next_action="Handle None input",
    )
    fragment = r.to_prompt_fragment()
    assert "FAILED" in fragment
    assert "Missing edge case" in fragment
    assert "Handle None input" in fragment
    assert "handles None" in fragment


def test_verification_result_error_fails_closed() -> None:
    r = VerificationResult.error("boom", verifier="goal_criteria")
    assert r.passed is False
    assert r.score == 0.0


# ---------------------------------------------------------------------------
# Replanner
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replanner_should_replan_on_tool_failure() -> None:
    r = Replanner(_FakeGateway([]))  # type: ignore[arg-type]
    goal = GoalState(objective="x", max_replans=3)
    assert await r.should_replan(goal, _obs(ok=False, error="file not found")) is True


@pytest.mark.asyncio
async def test_replanner_no_replan_when_budget_exhausted() -> None:
    r = Replanner(_FakeGateway([]))  # type: ignore[arg-type]
    goal = GoalState(objective="x", max_replans=1).with_replan()
    assert await r.should_replan(goal, _obs(ok=False, error="still failing")) is False


@pytest.mark.asyncio
async def test_replanner_no_replan_on_success() -> None:
    r = Replanner(_FakeGateway([]))  # type: ignore[arg-type]
    goal = GoalState(objective="x", max_replans=3)
    assert await r.should_replan(goal, _obs(ok=True, content="done")) is False


@pytest.mark.asyncio
async def test_replanner_replan_on_verification_failure() -> None:
    r = Replanner(_FakeGateway([]))  # type: ignore[arg-type]
    goal = GoalState(objective="x", max_replans=3)
    verification = VerificationResult(passed=False, score=0.2, verifier="goal_criteria")
    assert await r.should_replan(goal, _obs(ok=True, content="answer"), verification=verification) is True


@pytest.mark.asyncio
async def test_replanner_returns_new_plan() -> None:
    new_plan_json = (
        '{"goal":"revised goal","constraints":[],"steps":[{"index":0,"intent":"try differently",'
        '"tool":null,"operation":null,"args":{},"depends_on":[],"expected_output":null}],'
        '"termination_conditions":[],"risk":"low","estimated_cost_usd":0.0,'
        '"confidence":0.7,"unknowns":[]}'
    )
    r = Replanner(_FakeGateway([new_plan_json]))  # type: ignore[arg-type]
    new_plan = await r.replan(
        goal=GoalState(objective="original goal", max_replans=3),
        original_plan=_plan("original goal"),
        failure_context="tool failed",
        correlation_id=_CORR,
    )
    assert new_plan.goal == "revised goal"
    assert len(new_plan.steps) == 1
    assert new_plan.steps[0].intent == "try differently"


@pytest.mark.asyncio
async def test_replanner_falls_back_to_original_on_bad_json() -> None:
    r = Replanner(_FakeGateway(["totally invalid"]))  # type: ignore[arg-type]
    result = await r.replan(
        goal=GoalState(objective="x", max_replans=3),
        original_plan=_plan("original goal"),
        failure_context="tool failed",
        correlation_id=_CORR,
    )
    assert result.goal == "original goal"


# ---------------------------------------------------------------------------
# TaskResult
# ---------------------------------------------------------------------------


def test_task_result_has_phase1_fields() -> None:
    r = TaskResult(
        task_id="t1",
        ok=True,
        answer="done",
        steps_taken=3,
        replan_count=1,
        verification_passed=True,
        verification_score=0.92,
    )
    assert r.replan_count == 1
    assert r.verification_passed is True
    assert r.verification_score == pytest.approx(0.92, abs=0.01)


def test_task_result_backward_compat_defaults() -> None:
    r = TaskResult(task_id="t2", ok=False, error="oops", steps_taken=1)
    assert r.replan_count == 0
    assert r.verification_passed is None
    assert r.verification_score is None
