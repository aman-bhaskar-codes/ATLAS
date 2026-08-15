"""Phase 1 cognitive runtime tests — GoalState, Replanner, Verifier, TaskResult.

These tests verify the contracts added in Phase 1 without requiring real LLM
calls. They use fakes that match the existing test patterns in the orchestration
test suite.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from atlas.orchestration.goal import (
    GoalState, GoalVerifier, NullVerifier, VerificationResult,
)
from atlas.orchestration.replanner import Replanner
from atlas.orchestration.types import (
    Action, Capabilities, Observation, Plan, PlanStep, RiskLevel, TaskResult,
)


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
        text = self._responses.pop(0) if self._responses else '{"passed":true,"score":1.0,"criteria_results":{},"failure_reason":null,"suggestions":[]}'

        class _Resp:
            cost = type("c", (), {"input_tokens": 10, "output_tokens": 5})()

        _Resp.text = text  # type: ignore[attr-defined]
        return _Resp()


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
# GoalState
# ---------------------------------------------------------------------------

def test_goal_state_can_replan_within_budget() -> None:
    goal = GoalState(objective="test", max_replans=3)
    assert goal.can_replan() is True
    goal.record_replan()
    goal.record_replan()
    goal.record_replan()
    assert goal.can_replan() is False


def test_goal_state_to_prompt_fragment() -> None:
    goal = GoalState(
        objective="Build a widget",
        constraints=["no cloud", "under $1"],
        success_criteria=["tests pass", "linter clean"],
    )
    fragment = goal.to_prompt_fragment()
    assert "Build a widget" in fragment
    assert "tests pass" in fragment
    assert "no cloud" in fragment


def test_goal_state_update_progress() -> None:
    goal = GoalState(objective="x")
    goal.update_progress(0.5, "halfway")
    assert goal.progress == 0.5
    assert goal.current_state == "halfway"
    goal.update_progress(1.5)          # clamped to 1.0
    assert goal.progress == 1.0
    goal.update_progress(-0.1)         # clamped to 0.0
    assert goal.progress == 0.0


# ---------------------------------------------------------------------------
# NullVerifier
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_null_verifier_always_passes() -> None:
    v = NullVerifier()
    goal = GoalState(objective="anything", success_criteria=["a", "b"])
    result = await v.verify(goal, "answer", "context")
    assert result.passed is True
    assert result.score == 1.0


# ---------------------------------------------------------------------------
# GoalVerifier
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_goal_verifier_parses_passed_response() -> None:
    gw = _FakeGateway([
        '{"passed":true,"score":0.9,"criteria_results":{"tests pass":true},'
        '"failure_reason":null,"suggestions":[]}'
    ])
    v = GoalVerifier(gw)
    goal = GoalState(objective="test", success_criteria=["tests pass"])
    result = await v.verify(goal, "all 10 tests passed", "")
    assert result.passed is True
    assert result.score == pytest.approx(0.9, abs=0.01)
    assert result.criteria_results.get("tests pass") is True


@pytest.mark.asyncio
async def test_goal_verifier_parses_failed_response() -> None:
    gw = _FakeGateway([
        '{"passed":false,"score":0.2,"criteria_results":{"tests pass":false},'
        '"failure_reason":"Tests are still failing","suggestions":["Fix test_foo"]}'
    ])
    v = GoalVerifier(gw)
    goal = GoalState(objective="test", success_criteria=["tests pass"])
    result = await v.verify(goal, "I tried but tests still fail", "")
    assert result.passed is False
    assert result.score == pytest.approx(0.2, abs=0.01)
    assert result.failure_reason == "Tests are still failing"
    assert "Fix test_foo" in result.suggestions


@pytest.mark.asyncio
async def test_goal_verifier_fallback_on_bad_json() -> None:
    gw = _FakeGateway(["not json at all"])
    v = GoalVerifier(gw)
    goal = GoalState(objective="test", success_criteria=["pass"])
    result = await v.verify(goal, "something", "")
    # Must not raise; falls back to pass=True, score=0.5
    assert result.passed is True
    assert result.failure_reason == "verifier_error"


@pytest.mark.asyncio
async def test_goal_verifier_skips_when_no_criteria() -> None:
    gw = _FakeGateway([])  # No responses — verifier should not call gateway
    v = GoalVerifier(gw)
    goal = GoalState(objective="test", success_criteria=[])
    result = await v.verify(goal, "anything", "")
    assert result.passed is True
    assert len(gw._calls) == 0, "GoalVerifier must not call gateway when no criteria"


# ---------------------------------------------------------------------------
# VerificationResult
# ---------------------------------------------------------------------------

def test_verification_result_to_prompt_fragment_pass() -> None:
    r = VerificationResult(passed=True, score=0.95)
    assert "PASSED" in r.to_prompt_fragment()
    assert "0.95" in r.to_prompt_fragment()


def test_verification_result_to_prompt_fragment_fail() -> None:
    r = VerificationResult(
        passed=False, score=0.3,
        failure_reason="Missing edge case",
        suggestions=["Handle None input"],
    )
    fragment = r.to_prompt_fragment()
    assert "FAILED" in fragment
    assert "Missing edge case" in fragment
    assert "Handle None input" in fragment


# ---------------------------------------------------------------------------
# Replanner
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_replanner_should_replan_on_tool_failure() -> None:
    gw = _FakeGateway([])
    r = Replanner(gw)  # type: ignore[arg-type]
    goal = GoalState(objective="x", max_replans=3)
    obs = _obs(ok=False, error="file not found")
    assert await r.should_replan(goal, obs) is True


@pytest.mark.asyncio
async def test_replanner_no_replan_when_budget_exhausted() -> None:
    gw = _FakeGateway([])
    r = Replanner(gw)  # type: ignore[arg-type]
    goal = GoalState(objective="x", max_replans=1)
    goal.record_replan()  # budget exhausted
    obs = _obs(ok=False, error="still failing")
    assert await r.should_replan(goal, obs) is False


@pytest.mark.asyncio
async def test_replanner_no_replan_on_success() -> None:
    gw = _FakeGateway([])
    r = Replanner(gw)  # type: ignore[arg-type]
    goal = GoalState(objective="x", max_replans=3)
    obs = _obs(ok=True, content="done")
    assert await r.should_replan(goal, obs) is False


@pytest.mark.asyncio
async def test_replanner_replan_on_verification_failure() -> None:
    gw = _FakeGateway([])
    r = Replanner(gw)  # type: ignore[arg-type]
    goal = GoalState(objective="x", max_replans=3)
    obs = _obs(ok=True, content="answer")
    verification = VerificationResult(passed=False, score=0.2)
    assert await r.should_replan(goal, obs, verification=verification) is True


@pytest.mark.asyncio
async def test_replanner_returns_new_plan() -> None:
    new_plan_json = (
        '{"goal":"revised goal","constraints":[],"steps":[{"index":0,"intent":"try differently",'
        '"tool":null,"operation":null,"args":{},"depends_on":[],"expected_output":null}],'
        '"termination_conditions":[],"risk":"low","estimated_cost_usd":0.0,'
        '"confidence":0.7,"unknowns":[]}'
    )
    gw = _FakeGateway([new_plan_json])
    r = Replanner(gw)  # type: ignore[arg-type]
    goal = GoalState(objective="original goal", max_replans=3)
    original = _plan("original goal")

    new_plan = await r.replan(
        goal=goal,
        original_plan=original,
        failure_context="tool failed",
        correlation_id="test-corr",  # type: ignore[arg-type]
    )
    assert new_plan.goal == "revised goal"
    assert len(new_plan.steps) == 1
    assert new_plan.steps[0].intent == "try differently"


@pytest.mark.asyncio
async def test_replanner_falls_back_to_original_on_bad_json() -> None:
    gw = _FakeGateway(["totally invalid"])
    r = Replanner(gw)  # type: ignore[arg-type]
    goal = GoalState(objective="x", max_replans=3)
    original = _plan("original goal")

    result = await r.replan(
        goal=goal,
        original_plan=original,
        failure_context="tool failed",
        correlation_id="test-corr",  # type: ignore[arg-type]
    )
    # Falls back to original plan rather than crashing
    assert result.goal == "original goal"


# ---------------------------------------------------------------------------
# TaskResult Phase 1 fields
# ---------------------------------------------------------------------------

def test_task_result_has_phase1_fields() -> None:
    """TaskResult must carry replan_count and verification fields."""
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
    """Old callers that don't set Phase 1 fields must still work."""
    r = TaskResult(task_id="t2", ok=False, error="oops", steps_taken=1)
    assert r.replan_count == 0
    assert r.verification_passed is None
    assert r.verification_score is None
