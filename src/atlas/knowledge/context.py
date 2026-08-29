"""Research-grade context assembly (§10).

WHY this exists: retrieval + rerank + selection hand synthesis a *ranked* list of
Evidence, but "top-k by score" is not a research context. A good context is
budget-aware (fits the model window without silent overflow), diverse (one loud
source cannot crowd out the rest), free of near-duplicate quotes (they waste
budget and fake consensus), and keeps contradicting evidence ADJACENT so the model
sees both sides of a conflict instead of one truncated half (§30).

The ONE invariant this module protects: `[n]` markers are numbered by evidence
LIST ORDER in three places — the numbered context text, `CitationEngine.build`,
and the answer's `.citations`. So `assemble()` returns a single `included` list;
callers MUST feed that exact list to all three. `AssembledContext.text` is already
rendered from `included`, so alignment is guaranteed by construction.

Honesty (§22): every dropped quote is counted with a reason. A caller that dropped
evidence for budget must NOT imply it covered everything it retrieved.

Determinism: no model call, no randomness. Same evidence in → same context out, so
the assembler is replayable and testable without the network.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from atlas.knowledge.domain import Contradiction, Evidence, SecurityStatus, SourceType
from atlas.knowledge.injection import untrusted_prefix

# Sources whose content is DATA, never authority (§23). Framed in-line so the
# model treats their quotes as untrusted even when they made it into evidence.
_UNTRUSTED_TYPES = frozenset({SourceType.WEB_PAGE, SourceType.BROWSER_PAGE, SourceType.RSS, SourceType.PUBLIC_API})

_WORD = re.compile(r"[a-z0-9]+")


def _evidence_line(index: int, ev: Evidence) -> str:
    """Render one numbered evidence line — the SINGLE source of truth for the
    format, shared by budget accounting and the final context text so the two can
    never disagree on a line's length."""
    framing = ""
    if ev.source in _UNTRUSTED_TYPES or ev.provenance.get("security_status") == SecurityStatus.SUSPICIOUS.value:
        framing = untrusted_prefix(ev.source.value, SecurityStatus(ev.provenance.get("security_status", "SAFE"))) + "\n"
    return f'[{index}] {ev.title or ev.uri} ({ev.source.value}, authority={ev.authority:.2f})\n{framing}"{ev.quote}"\n'


def build_evidence_context(evidence: list[Evidence], *, max_chars: int = 6000) -> str:
    """Numbered evidence block with provenance framing for untrusted sources.

    Kept as a standalone function (and re-exported from `synthesis`) for callers
    and tests that only want the raw numbered block without diversity/dedup. The
    `ContextAssembler` is the richer, research-grade path.
    """
    lines: list[str] = []
    used = 0
    for i, ev in enumerate(evidence, start=1):
        line = _evidence_line(i, ev)
        if used + len(line) > max_chars:
            break
        lines.append(line)
        used += len(line)
    return "\n".join(lines)


def _tokens(text: str) -> frozenset[str]:
    return frozenset(_WORD.findall(text.lower()))


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if not inter:
        return 0.0
    return inter / len(a | b)


@dataclass(frozen=True)
class AssembledContext:
    """The finished context. `included` is the ordered list callers MUST feed to
    citations, the context text, and the answer — all three number `[n]` by this
    list's order, so any other list would desync markers."""

    text: str
    included: list[Evidence]
    dropped: list[Evidence] = field(default_factory=list)
    truncated: bool = False
    drop_reasons: dict[str, int] = field(default_factory=dict)

    @property
    def coverage_warning(self) -> str:
        """Non-empty when evidence was dropped — surface it so the answer never
        implies coverage it did not achieve (§22)."""
        if not self.dropped:
            return ""
        parts = ", ".join(f"{n} {reason}" for reason, n in sorted(self.drop_reasons.items()))
        total = len(self.included) + len(self.dropped)
        return f"Context assembly dropped {len(self.dropped)} of {total} quotes ({parts})."


class ContextAssembler:
    """Deterministically turns ranked Evidence into a research context.

    Pipeline (order matters): near-duplicate dedup → per-source diversity cap →
    contradiction-pair adjacency → budget-aware first-fit packing. Evidence that
    participates in a contradiction is protected from dedup/diversity drops so both
    sides of a conflict survive to the model (§30).
    """

    def __init__(self, *, max_chars: int = 6000, per_source_cap: int = 4, dedup_threshold: float = 0.9) -> None:
        self._max_chars = max_chars
        self._per_source_cap = per_source_cap
        self._dedup_threshold = dedup_threshold

    def assemble(
        self, evidence: list[Evidence], contradictions: list[Contradiction] | tuple[Contradiction, ...] = ()
    ) -> AssembledContext:
        if not evidence:
            return AssembledContext(text="", included=[], dropped=[], truncated=False, drop_reasons={})

        protected = _protected_ids(contradictions)
        reasons: dict[str, int] = {}
        dropped: list[Evidence] = []

        # 1) near-duplicate dedup — protected evidence is never a dedup victim.
        deduped: list[Evidence] = []
        kept_tokens: list[frozenset[str]] = []
        for ev in evidence:
            toks = _tokens(ev.quote)
            if ev.evidence_id not in protected and any(
                _jaccard(toks, seen) >= self._dedup_threshold for seen in kept_tokens
            ):
                dropped.append(ev)
                reasons["duplicate"] = reasons.get("duplicate", 0) + 1
                continue
            deduped.append(ev)
            kept_tokens.append(toks)

        # 2) per-source diversity cap — one source cannot dominate the window.
        diverse: list[Evidence] = []
        per_source: dict[SourceType, int] = {}
        for ev in deduped:
            count = per_source.get(ev.source, 0)
            if ev.evidence_id not in protected and count >= self._per_source_cap:
                dropped.append(ev)
                reasons["diversity"] = reasons.get("diversity", 0) + 1
                continue
            per_source[ev.source] = count + 1
            diverse.append(ev)

        # 3) contradiction adjacency — place each conflict partner next to its mate
        #    so the model sees both values together, preserving rank otherwise.
        ordered = _order_contradictions_adjacent(diverse, contradictions)

        # 4) budget-aware first-fit packing over the final order.
        included: list[Evidence] = []
        used = 0
        overflow = False
        for i, ev in enumerate(ordered, start=1):
            line = _evidence_line(i, ev)
            if included and used + len(line) > self._max_chars:
                overflow = True
                dropped.append(ev)
                reasons["budget"] = reasons.get("budget", 0) + 1
                continue
            included.append(ev)
            used += len(line)

        # Render text from `included` so [n] in the text == citation index == order.
        text = "\n".join(_evidence_line(i, ev) for i, ev in enumerate(included, start=1))
        return AssembledContext(text=text, included=included, dropped=dropped, truncated=overflow, drop_reasons=reasons)


def _protected_ids(contradictions: list[Contradiction] | tuple[Contradiction, ...]) -> frozenset[str]:
    ids: set[str] = set()
    for c in contradictions:
        ids.add(c.evidence_id_a)
        ids.add(c.evidence_id_b)
    return frozenset(ids)


def _order_contradictions_adjacent(
    evidence: list[Evidence], contradictions: list[Contradiction] | tuple[Contradiction, ...]
) -> list[Evidence]:
    """Stable reorder: keep rank order, but pull each contradiction's partner to sit
    directly after the first-seen member of the pair. No-op when neither member is
    present (a partner dropped upstream just leaves the survivor in place)."""
    if not contradictions:
        return list(evidence)
    by_id = {ev.evidence_id: ev for ev in evidence}
    # Deterministic: process pairs in the order the detector reported them.
    partner: dict[str, str] = {}
    for c in contradictions:
        if c.evidence_id_a in by_id and c.evidence_id_b in by_id:
            partner.setdefault(c.evidence_id_a, c.evidence_id_b)

    result: list[Evidence] = []
    placed: set[str] = set()
    for ev in evidence:
        if ev.evidence_id in placed:
            continue
        result.append(ev)
        placed.add(ev.evidence_id)
        mate_id = partner.get(ev.evidence_id)
        if mate_id and mate_id not in placed:
            result.append(by_id[mate_id])
            placed.add(mate_id)
    return result
