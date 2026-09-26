"""Immutable records for the research surface (Phase 1, Slice R1).

WHY typed records, not raw dicts: the ``knowledge`` tool returns an untyped
observation payload (``tools/research.py::_answer_payload`` + the deep-research
envelope). A persisted, API-facing surface needs a STABLE, typed contract for
that grounded result — one the store round-trips exactly and the REST/SSE layer
and the Perplexity frontend consume. These models are the seam between the tool's
loose dict and the durable session envelope; ``build_result`` is the single
adapter that maps one into the other (honest status included — an answer the
fabric refused for lack of evidence lands as ``REFUSED``, never a fabricated
answer, §54/§69).

Records are frozen — a session, once persisted terminal, never mutates in place;
a follow-up mints a new, linked session.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, model_validator


class ResearchStatus(StrEnum):
    """A persisted research session's lifecycle state.

    ``RUNNING`` is the non-terminal state a backgrounded run sits in until its
    result lands (Slice R3); the synchronous R1 path never persists it. The three
    terminal states distinguish an answered question (``COMPLETED``), an HONEST
    refusal the fabric returned for want of grounding evidence (``REFUSED``, §54),
    and an execution failure — a denied/halted dispatch or a tool error
    (``FAILED``). Stored and indexed so the session list can filter without
    opening the payload.
    """

    RUNNING = "running"
    COMPLETED = "completed"
    REFUSED = "refused"
    FAILED = "failed"


TERMINAL_RESEARCH_STATUSES = frozenset(
    {ResearchStatus.COMPLETED.value, ResearchStatus.REFUSED.value, ResearchStatus.FAILED.value}
)


class ResearchCitation(BaseModel):
    """One inline citation resolved by the fabric's ``CitationEngine`` — the anchor
    a ``[n]`` marker in the answer text points at."""

    model_config = {"frozen": True}
    index: int = 0
    title: str = ""
    uri: str = ""
    quote: str = ""


class ResearchSource(BaseModel):
    """One retrieved source for the source rail. Distinct from a citation: every
    cited source is a source, but the rail may list retrieved sources the synthesis
    did not end up quoting."""

    model_config = {"frozen": True}
    title: str = ""
    uri: str = ""
    quote: str = ""


class ResearchContradiction(BaseModel):
    """A surfaced disagreement between sources — carried verbatim, never averaged
    away (§54)."""

    model_config = {"frozen": True}
    key: str = ""
    description: str = ""


class ResearchAnswer(BaseModel):
    """The grounded, cited answer for a session — the typed projection of the
    ``knowledge`` tool's ``_answer_payload``.

    ``answered`` is the fabric's honesty gate: ``False`` means it refused for want
    of evidence and ``refusal_reason`` says why; the surface must render that
    refusal, never invent a green answer (§54/§69). ``coverage_warning`` carries
    the deep-research envelope's "no sources retrieved" notice when present.
    """

    model_config = {"frozen": True}
    text: str = ""
    answered: bool = False
    confidence: float = 0.0
    mode: str = ""
    citations: tuple[ResearchCitation, ...] = ()
    refusal_reason: str = ""
    contradictions: tuple[ResearchContradiction, ...] = ()
    degraded: bool = False
    degradation_reason: str = ""
    coverage_warning: str = ""


class ResearchSessionRecord(BaseModel):
    """The durable envelope one research question is persisted as (Slice R1).

    Stores the question, the operation used, the grounded ``ResearchAnswer``, the
    retrieved source rail, and the multi-round investigation trace summary (rounds,
    stop reason, discovery counts) the supervisor produced — everything the
    Perplexity surface renders, all with a real backend origin (§69).

    ``parent_session_id`` links a follow-up to the session it continues (Slice R2),
    giving a research thread traceable lineage; it is ``None`` for a first
    question. ``status`` is derived from the answer when not passed explicitly (so
    terminal call sites stay terse) and is a stored, indexed field.
    """

    model_config = {"frozen": True}
    session_id: str
    correlation_id: str = ""
    parent_session_id: str | None = None
    question: str = ""
    mode: str = "deep_research"
    status: str = ""
    answer: ResearchAnswer | None = None
    sources: tuple[ResearchSource, ...] = ()
    # Multi-round investigation trace (supervisor/runner envelope). Kept as loose
    # JSON-able summary fields so the trace schema can grow without a migration.
    stop_reason: str = ""
    total_rounds: int = 0
    total_discovered: int = 0
    open_questions: int = 0
    rounds: tuple[dict[str, Any], ...] = ()
    questions: tuple[dict[str, Any], ...] = ()
    error: str | None = None
    created_ts: str = ""
    updated_ts: str = ""

    @model_validator(mode="after")
    def _derive_status(self) -> ResearchSessionRecord:
        """Fill ``status`` from the outcome when it was not set explicitly.

        A freshly built terminal record that passes ``answer``/``error`` but no
        ``status`` gets it derived here: an execution error → ``FAILED``, an
        unanswered (honestly refused) answer → ``REFUSED``, an answered one →
        ``COMPLETED``, and neither (a running stub) → ``RUNNING``. A loaded record
        already carries ``status`` in its payload, so this is a no-op on
        rehydration — the round-trip stays exact.
        """
        if not self.status:
            if self.error:
                derived = ResearchStatus.FAILED.value
            elif self.answer is None:
                derived = ResearchStatus.RUNNING.value
            elif self.answer.answered:
                derived = ResearchStatus.COMPLETED.value
            else:
                derived = ResearchStatus.REFUSED.value
            object.__setattr__(self, "status", derived)
        return self


def _citations(payload: dict[str, Any]) -> tuple[ResearchCitation, ...]:
    return tuple(
        ResearchCitation(
            index=int(c.get("index", i + 1) or (i + 1)),
            title=str(c.get("title", "") or ""),
            uri=str(c.get("uri", "") or ""),
            quote=str(c.get("quote", "") or ""),
        )
        for i, c in enumerate(payload.get("citations", []) or [])
        if isinstance(c, dict)
    )


def _contradictions(payload: dict[str, Any]) -> tuple[ResearchContradiction, ...]:
    return tuple(
        ResearchContradiction(
            key=str(c.get("key", "") or ""),
            description=str(c.get("description", "") or ""),
        )
        for c in payload.get("contradictions", []) or []
        if isinstance(c, dict)
    )


def answer_from_payload(payload: dict[str, Any]) -> ResearchAnswer:
    """Map the ``knowledge`` tool's ``_answer_payload`` dict into a typed answer.

    Reads structurally with defaults so a partial/degraded payload never raises —
    the surface degrades honestly rather than crashing. ``coverage_warning`` is
    lifted from the enclosing deep-research envelope by ``build_result`` (it does
    not live on the answer payload itself).
    """
    return ResearchAnswer(
        text=str(payload.get("text", "") or ""),
        answered=bool(payload.get("answered", False)),
        confidence=float(payload.get("confidence", 0.0) or 0.0),
        mode=str(payload.get("mode", "") or ""),
        citations=_citations(payload),
        refusal_reason=str(payload.get("refusal_reason", "") or ""),
        contradictions=_contradictions(payload),
        degraded=bool(payload.get("degraded", False)),
        degradation_reason=str(payload.get("degradation_reason", "") or ""),
    )


def _sources(payload: dict[str, Any]) -> tuple[ResearchSource, ...]:
    return tuple(
        ResearchSource(
            title=str(f.get("title", "") or ""),
            uri=str(f.get("uri", "") or ""),
            quote=str(f.get("quote", "") or ""),
        )
        for f in payload.get("findings", []) or []
        if isinstance(f, dict)
    )


class ResearchOutcome(BaseModel):
    """The parsed, typed result of one governed research dispatch — the answer, the
    source rail, and the investigation-trace summary. A pure projection of the
    tool's observation content, assembled by ``build_result`` so the service stays
    thin and the mapping is unit-testable in isolation."""

    model_config = {"frozen": True}
    answer: ResearchAnswer
    sources: tuple[ResearchSource, ...] = ()
    stop_reason: str = ""
    total_rounds: int = 0
    total_discovered: int = 0
    open_questions: int = 0
    rounds: tuple[dict[str, Any], ...] = ()
    questions: tuple[dict[str, Any], ...] = ()


def build_result(content: Any) -> ResearchOutcome:
    """Assemble a typed ``ResearchOutcome`` from a ``knowledge``-tool observation.

    Accepts the observation content for any research operation: the flat
    ``_answer_payload`` (``search``) or the deep-research/round envelope that nests
    the answer under ``answer`` and adds ``findings`` + a round trace. A
    ``coverage_warning`` on the envelope is folded onto the answer so the surface
    renders the honest "no sources" notice. Defensive against a non-dict content
    (e.g. a degraded tool result) — yields an unanswered answer rather than
    raising.
    """
    if not isinstance(content, dict):
        return ResearchOutcome(answer=ResearchAnswer(refusal_reason="no research payload returned"))

    envelope: dict[str, Any] = content
    answer_payload = envelope.get("answer")
    if isinstance(answer_payload, dict):
        answer = answer_from_payload(answer_payload)
    else:
        # A flat search payload IS the answer payload.
        answer = answer_from_payload(envelope)

    coverage = str(envelope.get("coverage_warning", "") or "")
    if coverage:
        answer = answer.model_copy(update={"coverage_warning": coverage})

    return ResearchOutcome(
        answer=answer,
        sources=_sources(envelope),
        stop_reason=str(envelope.get("stop_reason", "") or ""),
        total_rounds=int(envelope.get("total_rounds", 0) or 0),
        total_discovered=int(envelope.get("total_discovered", 0) or 0),
        open_questions=int(envelope.get("open_questions", 0) or 0),
        rounds=tuple(r for r in envelope.get("rounds", []) or [] if isinstance(r, dict)),
        questions=tuple(q for q in envelope.get("questions", []) or [] if isinstance(q, dict)),
    )
