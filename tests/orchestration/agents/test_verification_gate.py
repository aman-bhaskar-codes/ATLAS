"""The verification gate on a delegated run.

A specialist reporting SUCCEEDED means its own loop finished. These tests pin the
rule that follows from that: a multi-agent run may only report VERIFIED when an
independent check said so, and every other path — no verifier, inapplicable
verifier, crashed verifier, contradicting branches, an incomplete graph — lands
on UNCERTAIN or REJECTED instead.
"""

from __future__ import annotations

from typing import Any

from atlas.infra.cognition import GoalState, TaskDomain, VerificationResult
from atlas.infra.ids import CorrelationId
from atlas.orchestration.agents.types import AgentRole, RunOutcome, SubTaskStatus
from tests.orchestration.agents._fakes import RecordingEvents, ScriptedSpecialist
from tests.orchestration.agents.test_supervisor import build, delegating, run, st


class ScriptedVerifier:
    """Returns a canned VerificationResult, or raises."""

    name = "scripted"

    def __init__(self, outcome: VerificationResult | Exception) -> None:
        self._outcome = outcome
        self.calls: list[tuple[GoalState, str, tuple[Any, ...]]] = []

    async def verify(
        self,
        goal: GoalState,
        answer: str,
        correlation_id: CorrelationId,
        context: str = "",
        domain: TaskDomain = TaskDomain.UNKNOWN,
        evidence: tuple[Any, ...] = (),
    ) -> VerificationResult:
        self.calls.append((goal, answer, evidence))
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


def passing() -> VerificationResult:
    return VerificationResult(passed=True, score=0.9, verifier="scripted")


def failing() -> VerificationResult:
    return VerificationResult(passed=False, score=0.1, verifier="scripted", failure_reason="criteria unmet")


async def test_passing_verification_yields_verified() -> None:
    verifier = ScriptedVerifier(passing())
    sup = build(delegating(st("st1"), st("st2")), ScriptedSpecialist(), verifier=verifier)

    result = await run(sup)

    assert result.outcome is RunOutcome.VERIFIED
    assert result.verified is True
    assert len(verifier.calls) == 1


async def test_verifier_sees_the_original_goal_and_per_subtask_evidence() -> None:
    """It must check the REQUEST, not a specialist's restatement of it."""
    verifier = ScriptedVerifier(passing())
    sup = build(delegating(st("st1", role=AgentRole.RESEARCHER), st("st2")), ScriptedSpecialist(), verifier=verifier)

    result = await run(sup)

    goal, answer, evidence = verifier.calls[0]
    assert goal.objective == "do the thing"  # the parent intent's objective
    assert answer == result.answer
    assert [e.source for e in evidence] == ["st1", "st2"]
    assert all(e.ok for e in evidence)


async def test_failing_verification_is_rejected_not_completed_quietly() -> None:
    sup = build(delegating(st("st1"), st("st2")), ScriptedSpecialist(), verifier=ScriptedVerifier(failing()))

    result = await run(sup)

    assert result.outcome is RunOutcome.REJECTED
    assert result.verified is False
    assert result.answer  # the work is still returned, it is just not endorsed


async def test_crashed_verifier_fails_closed_to_uncertain() -> None:
    """A broken verifier must never be indistinguishable from a pass — and must
    not be reported as proof the answer is wrong either."""
    sup = build(
        delegating(st("st1"), st("st2")),
        ScriptedSpecialist(),
        verifier=ScriptedVerifier(RuntimeError("verifier exploded")),
    )

    result = await run(sup)

    assert result.outcome is RunOutcome.UNCERTAIN
    assert result.verification is not None
    assert result.verification.passed is False
    assert "verifier_error" in (result.verification.failure_reason or "")


async def test_no_verifier_wired_is_uncertain_never_verified() -> None:
    sup = build(delegating(st("st1"), st("st2")), ScriptedSpecialist())

    result = await run(sup)

    assert result.ok is True
    assert result.verification is None
    assert result.outcome is RunOutcome.UNCERTAIN


async def test_not_applicable_verification_is_uncertain() -> None:
    """`passed=True, verifier="none"` means nothing was checked."""
    sup = build(
        delegating(st("st1"), st("st2")),
        ScriptedSpecialist(),
        verifier=ScriptedVerifier(VerificationResult.not_applicable("no criteria")),
    )

    assert (await run(sup)).outcome is RunOutcome.UNCERTAIN


async def test_contradicting_branches_downgrade_a_passing_verification() -> None:
    """Conflict is reported, never resolved by majority — the run goes uncertain."""

    class ContradictingSpecialist(ScriptedSpecialist):
        async def run(self, **kwargs: Any) -> Any:
            result = await super().run(**kwargs)
            negate = "not " if result.subtask_id == "st2" else ""
            return result.model_copy(
                update={"output": f"The connection pool is {negate}shared between worker threads."}
            )

    sup = build(delegating(st("st1"), st("st2")), ContradictingSpecialist(), verifier=ScriptedVerifier(passing()))

    result = await run(sup)

    assert result.conflicts
    assert result.conflicts[0].kind == "polarity"
    assert result.outcome is RunOutcome.UNCERTAIN


async def test_run_token_budget_abandons_later_batches_and_blocks_verified() -> None:
    """Per-subtask limits bound one specialist; only this bounds the graph."""
    spec = ScriptedSpecialist()  # 100 tokens per subtask
    sup = build(
        delegating(st("st1"), st("st2", "st1"), st("st3", "st2")),
        spec,
        verifier=ScriptedVerifier(passing()),
        max_total_tokens=150,
    )

    result = await run(sup)

    assert spec.ran == ["st1", "st2"]  # st3 never started
    assert result.abandoned == ("st3",)
    by_id = {r.subtask_id: r for r in result.results}
    assert by_id["st3"].status is SubTaskStatus.SKIPPED
    assert "token budget" in (by_id["st3"].error or "")
    # An incomplete graph cannot produce a verified answer, even if the
    # verifier liked what the surviving branches produced.
    assert result.outcome is RunOutcome.UNCERTAIN


async def test_zero_budget_means_unbounded_not_abandon_everything() -> None:
    spec = ScriptedSpecialist()
    sup = build(delegating(st("st1"), st("st2", "st1")), spec, max_total_tokens=0)

    result = await run(sup)

    assert spec.ran == ["st1", "st2"]
    assert result.abandoned == ()


async def test_total_failure_is_failed_not_uncertain() -> None:
    sup = build(
        delegating(st("st1"), st("st2")),
        ScriptedSpecialist({"st1": SubTaskStatus.FAILED, "st2": SubTaskStatus.FAILED}),
        verifier=ScriptedVerifier(passing()),
    )

    result = await run(sup)

    assert result.ok is False
    assert result.outcome is RunOutcome.FAILED


async def test_verification_is_traceable_in_the_event_stream() -> None:
    events = RecordingEvents()
    sup = build(
        delegating(st("st1"), st("st2")),
        ScriptedSpecialist(),
        events=events,
        verifier=ScriptedVerifier(failing()),
    )

    await run(sup)

    assert "agents.verified" in events.kinds()
    emitted = next(e for e in events.emitted if e["kind"] == "agents.verified")
    assert emitted["outcome"] == RunOutcome.REJECTED.value
    assert emitted["verifier"] == "scripted"
    assert emitted["verification_passed"] is False
