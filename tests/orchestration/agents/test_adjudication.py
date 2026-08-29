"""Deterministic conflict detection between specialist branches.

The contract under test is narrow on purpose: catch the contradictions a machine
can prove, stay silent otherwise. A false positive downgrades a good answer to
`uncertain`, so the no-false-positive cases matter as much as the detections.
"""

from __future__ import annotations

from atlas.orchestration.agents.adjudication import detect_conflicts
from atlas.orchestration.agents.types import AgentRole, SubTaskResult, SubTaskStatus


def _result(subtask_id: str, output: str, *, ok: bool = True) -> SubTaskResult:
    return SubTaskResult(
        subtask_id=subtask_id,
        role=AgentRole.RESEARCHER,
        status=SubTaskStatus.SUCCEEDED if ok else SubTaskStatus.FAILED,
        output=output if ok else "",
        error=None if ok else output,
    )


def test_detects_negated_restatement() -> None:
    conflicts = detect_conflicts(
        (
            _result("st1", "The retry handler is safe to call concurrently."),
            _result("st2", "The retry handler is not safe to call concurrently."),
        )
    )
    assert len(conflicts) == 1
    assert conflicts[0].kind == "polarity"
    assert {conflicts[0].subtask_a, conflicts[0].subtask_b} == {"st1", "st2"}


def test_detects_contraction_negation() -> None:
    """ "isn't" must reduce to "is" + a negation, or the skeletons would differ."""
    conflicts = detect_conflicts(
        (
            _result("st1", "The database migration is applied to the live schema."),
            _result("st2", "The database migration isn't applied to the live schema."),
        )
    )
    assert [c.kind for c in conflicts] == ["polarity"]


def test_detects_numeric_disagreement() -> None:
    conflicts = detect_conflicts(
        (
            _result("st1", "The suite contains 1,388 tests across the package."),
            _result("st2", "The suite contains 1200 tests across the package."),
        )
    )
    assert [c.kind for c in conflicts] == ["numeric"]


def test_number_formatting_alone_is_not_a_conflict() -> None:
    """1,000 and 1000.0 are the same claim written two ways."""
    assert (
        detect_conflicts(
            (
                _result("st1", "The index holds 1,000 documents in total right now."),
                _result("st2", "The index holds 1000.0 documents in total right now."),
            )
        )
        == ()
    )


def test_unrelated_statements_do_not_conflict() -> None:
    assert (
        detect_conflicts(
            (
                _result("st1", "The scheduler uses a semaphore to cap concurrency."),
                _result("st2", "The embedder posts one request per input document."),
            )
        )
        == ()
    )


def test_agreement_is_not_a_conflict() -> None:
    assert (
        detect_conflicts(
            (
                _result("st1", "The safety engine denies unknown operations by default."),
                _result("st2", "The safety engine denies unknown operations by default."),
            )
        )
        == ()
    )


def test_short_fragments_are_ignored() -> None:
    """Below the skeleton floor, "identical sentence" carries no information."""
    assert detect_conflicts((_result("st1", "Yes. Done."), _result("st2", "No. Done."))) == ()


def test_self_contradiction_within_one_subtask_is_not_a_cross_branch_conflict() -> None:
    assert (
        detect_conflicts(
            (
                _result(
                    "st1",
                    "The cache is enabled for every request. The cache is not enabled for every request.",
                ),
            )
        )
        == ()
    )


def test_failed_and_empty_results_are_ignored() -> None:
    """An error string is not a claim; treating it as one invents conflicts."""
    assert (
        detect_conflicts(
            (
                _result("st1", "The provider endpoint is reachable from this host."),
                _result("st2", "The provider endpoint is not reachable from this host.", ok=False),
                SubTaskResult(subtask_id="st3", role=AgentRole.CODER, status=SubTaskStatus.SKIPPED),
            )
        )
        == ()
    )


def test_conflict_list_is_bounded_and_ordered() -> None:
    """Deterministic output: the same inputs must always yield the same report."""
    pairs = tuple(
        _result(f"st{i}", f"Component number alpha beta gamma reports {i} open handles.") for i in range(1, 9)
    )
    conflicts = detect_conflicts(pairs)
    assert 0 < len(conflicts) <= 5
    assert list(conflicts) == sorted(conflicts, key=lambda c: (c.subtask_a, c.subtask_b, c.kind, c.statement_a))
    assert detect_conflicts(pairs) == conflicts
