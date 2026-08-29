"""ContextAssembler tests (§10, §22, §30) — deterministic, no network.

The invariant under test: `assemble()` returns ONE `included` list, and the
rendered `text` numbers `[n]` by that list's order — so citations, context, and
answer can share it without marker desync. Plus: near-duplicate quotes are
dropped, one source can't dominate, contradiction partners stay adjacent, and
every drop is counted honestly.
"""

from __future__ import annotations

from datetime import UTC, datetime

from atlas.knowledge.context import AssembledContext, ContextAssembler, build_evidence_context
from atlas.knowledge.domain import Contradiction, Evidence, SourceType


def _ev(n: int, quote: str, *, source: SourceType = SourceType.LOCAL_FILE, authority: float = 0.8) -> Evidence:
    return Evidence(
        evidence_id=f"ev_{n}",
        document_id=f"doc_{n}",
        chunk_id=f"chk_{n}",
        source=source,
        quote=quote,
        title=f"Doc {n}",
        uri=f"https://ex.test/{n}",
        retrieved_at=datetime.now(UTC),
        authority=authority,
    )


# ── empty input ──────────────────────────────────────────────────────────
def test_empty_evidence_yields_empty_context() -> None:
    out = ContextAssembler().assemble([])
    assert out == AssembledContext(text="", included=[], dropped=[], truncated=False, drop_reasons={})
    assert out.coverage_warning == ""


# ── ordering / marker alignment ────────────────────────────────────────────
def test_included_order_matches_numbered_text() -> None:
    evidence = [_ev(1, "first fact about engines"), _ev(2, "second distinct fact")]
    out = ContextAssembler().assemble(evidence)
    assert [e.evidence_id for e in out.included] == ["ev_1", "ev_2"]
    # [n] in text is numbered by included order — the whole point.
    assert out.text.startswith("[1] Doc 1")
    assert "[2] Doc 2" in out.text
    assert not out.truncated


# ── near-duplicate dedup ────────────────────────────────────────────────────
def test_near_duplicate_quotes_are_dropped() -> None:
    evidence = [
        _ev(1, "adaptation is measured with held out benchmarks"),
        _ev(2, "adaptation is measured with held out benchmarks today"),  # ~dup
        _ev(3, "an entirely different unrelated statement here"),
    ]
    out = ContextAssembler(dedup_threshold=0.7).assemble(evidence)
    ids = [e.evidence_id for e in out.included]
    assert "ev_2" not in ids  # near-duplicate removed
    assert ids == ["ev_1", "ev_3"]
    assert out.drop_reasons.get("duplicate") == 1
    assert "duplicate" in out.coverage_warning


# ── per-source diversity cap ────────────────────────────────────────────────
def test_one_source_cannot_dominate() -> None:
    evidence = [_ev(i, f"distinct web statement number {i}", source=SourceType.WEB_PAGE) for i in range(6)]
    out = ContextAssembler(per_source_cap=2).assemble(evidence)
    assert len(out.included) == 2
    assert out.drop_reasons.get("diversity") == 4


def test_diversity_cap_is_per_source_not_global() -> None:
    evidence = [
        _ev(1, "web statement alpha", source=SourceType.WEB_PAGE),
        _ev(2, "web statement beta", source=SourceType.WEB_PAGE),
        _ev(3, "web statement gamma", source=SourceType.WEB_PAGE),  # over cap
        _ev(4, "local statement delta", source=SourceType.LOCAL_FILE),
    ]
    out = ContextAssembler(per_source_cap=2).assemble(evidence)
    ids = [e.evidence_id for e in out.included]
    assert ids == ["ev_1", "ev_2", "ev_4"]  # third web dropped, local kept
    assert out.drop_reasons.get("diversity") == 1


# ── contradiction adjacency + protection ────────────────────────────────────
def test_contradiction_partners_are_made_adjacent() -> None:
    evidence = [
        _ev(1, "output is 200 megawatts"),
        _ev(2, "some unrelated filler statement about turbines"),
        _ev(3, "output is 900 megawatts"),
    ]
    contra = [Contradiction(key="output", description="disagree", evidence_id_a="ev_1", evidence_id_b="ev_3")]
    out = ContextAssembler().assemble(evidence, contra)
    ids = [e.evidence_id for e in out.included]
    # ev_3 pulled up to sit right after ev_1; filler slides down.
    assert ids == ["ev_1", "ev_3", "ev_2"]


def test_contradiction_evidence_survives_diversity_cap() -> None:
    evidence = [
        _ev(1, "web statement one", source=SourceType.WEB_PAGE),
        _ev(2, "web statement two", source=SourceType.WEB_PAGE),
        _ev(3, "web says value is 900", source=SourceType.WEB_PAGE),  # would be capped
    ]
    contra = [Contradiction(key="v", description="d", evidence_id_a="ev_1", evidence_id_b="ev_3")]
    out = ContextAssembler(per_source_cap=2).assemble(evidence, contra)
    ids = [e.evidence_id for e in out.included]
    assert "ev_3" in ids  # protected from the diversity drop so both sides survive


# ── budget-aware packing + honesty ──────────────────────────────────────────
def test_budget_truncation_is_reported_not_silent() -> None:
    evidence = [_ev(i, f"quote {i} " + "x" * 300) for i in range(10)]
    out = ContextAssembler(max_chars=800, per_source_cap=99).assemble(evidence)
    assert out.truncated is True
    assert len(out.text) <= 800
    assert out.drop_reasons.get("budget", 0) >= 1
    assert "budget" in out.coverage_warning
    # at least the first quote always survives even if it alone exceeds budget.
    assert out.included[0].evidence_id == "ev_0"


# ── untrusted framing preserved via shared line renderer ────────────────────
def test_untrusted_sources_are_framed_in_text() -> None:
    out = ContextAssembler().assemble([_ev(1, "web claim", source=SourceType.WEB_PAGE, authority=0.5)])
    assert "UNTRUSTED CONTENT" in out.text
    local = ContextAssembler().assemble([_ev(2, "local fact")])
    assert "UNTRUSTED CONTENT" not in local.text


# ── build_evidence_context standalone still behaves (moved, not changed) ────
def test_standalone_builder_still_frames_and_bounds() -> None:
    web = _ev(1, "web claim", source=SourceType.WEB_PAGE, authority=0.5)
    assert "UNTRUSTED CONTENT" in build_evidence_context([web])
    # Same shape as the original synthesis test: first-fit stops well under budget.
    big = [_ev(i, f"quote number {i} " + "x" * 200) for i in range(50)]
    assert len(build_evidence_context(big, max_chars=1000)) <= 1000
