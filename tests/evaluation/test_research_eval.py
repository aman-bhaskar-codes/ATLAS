"""Research-answer evaluation (R8): deterministic citation grounding.

The generic evaluators score substrings and regexes; a research answer's
distinguishing quality is that its citations resolve. These tests pin the pure
structural check — no model, CI-safe — and prove it composes with the existing
DeterministicEvaluator through the opt-in MatchSpec flag.
"""

from __future__ import annotations

from atlas.evaluation.evaluators import DeterministicEvaluator, check_citation_grounding
from atlas.evaluation.golden import GoldenTask, MatchSpec

_GROUNDED = "The sky scatters blue light [1] and Rayleigh explains it [2].\n\n[1] Optics — a\n[2] Rayleigh — b"
_DANGLING = "It follows from prior work [1] and later work [3].\n\n[1] Only source — a"
_NO_CITES = "A plain answer with no citation markers at all."


def test_grounded_answer_passes() -> None:
    grounded, criteria, reason = check_citation_grounding(_GROUNDED)
    assert grounded is True
    assert criteria == {"citations_present": True, "no_dangling_citations": True}
    assert reason is None


def test_citation_pointing_at_undefined_source_is_dangling() -> None:
    grounded, criteria, reason = check_citation_grounding(_DANGLING)
    assert grounded is False
    assert criteria["no_dangling_citations"] is False
    assert reason is not None and "[3]" in reason


def test_answer_without_citations_is_vacuously_grounded() -> None:
    grounded, criteria, _ = check_citation_grounding(_NO_CITES)
    assert grounded is True
    assert criteria["citations_present"] is False


def test_definition_line_with_extra_markers_counts_them_as_uses() -> None:
    # "[1] see also [2]" defines 1 and uses 2; 2 is undefined → dangling.
    grounded, _, reason = check_citation_grounding("Body cites [1].\n[1] Source one, see also [2]")
    assert grounded is False
    assert reason is not None and "[2]" in reason


async def test_deterministic_evaluator_enforces_grounding_when_opted_in() -> None:
    ev = DeterministicEvaluator()
    task = GoldenTask(id="r1", category="research", prompt="q", expected=MatchSpec(citations_grounded=True))

    ok = await ev.evaluate(task, _GROUNDED)
    bad = await ev.evaluate(task, _DANGLING)

    assert ok.passed is True
    assert bad.passed is False
    assert bad.criteria["no_dangling_citations"] is False


async def test_grounding_is_off_by_default_so_existing_suites_are_unaffected() -> None:
    ev = DeterministicEvaluator()
    task = GoldenTask(id="r2", category="analysis", prompt="q", expected=MatchSpec(contains_all=["work"]))

    result = await ev.evaluate(task, _DANGLING)  # dangling citation, but grounding not requested

    assert result.passed is True
    assert "no_dangling_citations" not in result.criteria
