"""Evidence pipeline — selection, contradiction, claims, verification (§30-33).

Evidence-first: answers are built FROM evidence, never the reverse. Selection
picks the best quote per chunk; contradiction detection surfaces disagreement
(NEVER averages it away, §30); claims extracted from synthesized text are
ground-checked against the evidence set before being called supported.
"""

from __future__ import annotations

import hashlib
import re

from atlas.infra.clock import Clock
from atlas.infra.ids import IdGenerator
from atlas.knowledge.bm25 import tokenize
from atlas.knowledge.domain import (
    Claim,
    ClaimStatus,
    Contradiction,
    Evidence,
    make_evidence_id,
)
from atlas.knowledge.retrieval import Candidate

_NUMBER_RE = re.compile(
    r"(-?\d+(?:\.\d+)?)\s*"
    r"(%|percent|usd|dollars|ms|seconds?|minutes?|hours?|days?|years?|gb|mb|kb|v\d+(?:\.\d+)*)?",
    re.I,
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


class EvidenceSelector:
    """Turn reranked candidates into pinned Evidence (one quote per chunk)."""

    def __init__(self, ids: IdGenerator, clock: Clock, *, max_evidence: int = 10) -> None:
        self._ids = ids
        self._clock = clock
        self._max = max_evidence

    def select(self, query: str, candidates: list[Candidate]) -> list[Evidence]:
        q_tokens = set(tokenize(query))
        picked: list[Evidence] = []
        seen_quotes: set[str] = set()
        for cand in candidates:
            if len(picked) >= self._max:
                break
            quote = _best_quote(cand.chunk.content, q_tokens)
            if not quote or quote in seen_quotes:
                continue
            seen_quotes.add(quote)
            doc = cand.document
            heading_loc = f"§ {cand.chunk.heading}" if cand.chunk.heading else f"chunk {cand.chunk.chunk_index}"
            ev = Evidence(
                evidence_id=make_evidence_id(doc.document_id, quote),
                document_id=doc.document_id,
                chunk_id=cand.chunk.chunk_id,
                source=doc.source_type,
                quote=quote,
                location=heading_loc,
                uri=doc.uri or doc.source_id,
                title=doc.title,
                retrieved_at=self._clock.now(),
                authority=doc.authority,
                confidence=_quote_confidence(quote, q_tokens, cand.rrf_score),
                provenance={
                    "source_type": doc.source_type.value,
                    "security_status": doc.security_status.value,
                    "freshness": doc.freshness,
                    "rrf_score": round(cand.rrf_score, 4),
                },
            ).with_hash()
            picked.append(ev)
        return picked


def _best_quote(chunk_text: str, q_tokens: set[str], *, max_len: int = 400) -> str:
    """Pick the sentence (or short chunk) with highest query-token overlap."""
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(chunk_text) if len(s.strip()) > 20]
    pool = sentences if sentences else [chunk_text.strip()]
    best = ""
    best_score = -1.0
    for s in pool:
        toks = set(tokenize(s))
        if not toks:
            continue
        score = len(q_tokens & toks) / max(len(q_tokens), 1)
        if score > best_score:
            best_score = score
            best = s
    quote = best[:max_len].strip()
    return quote


def _quote_confidence(quote: str, q_tokens: set[str], rrf: float) -> float:
    overlap = len(q_tokens & set(tokenize(quote))) / max(len(q_tokens), 1)
    return round(min(0.95, 0.3 + 0.4 * overlap + 0.3 * min(rrf * 20, 1.0)), 3)


class ContradictionDetector:
    """Detect numeric/value conflicts between evidence on the same topic (§30).

    Deterministic: group evidence by normalized topic tokens; within a group,
    conflicting extracted values from DIFFERENT documents are contradictions.
    We never average — both sides stay visible for synthesis.
    """

    def detect(self, query: str, evidence: list[Evidence]) -> list[Contradiction]:
        if len(evidence) < 2:
            return []
        q_tokens = sorted(tokenize(query))[:4]
        topic = "/".join(q_tokens) or "topic"
        out: list[Contradiction] = []
        # value → (evidence, canonical value)
        seen: list[tuple[str, Evidence]] = []
        for ev in evidence:
            for m in _NUMBER_RE.finditer(ev.quote):
                raw = m.group(0).strip().lower()
                if len(raw) < 2:
                    continue
                conflicting = next(
                    (
                        prev_ev
                        for prev_val, prev_ev in seen
                        if prev_ev.document_id != ev.document_id and _values_conflict(prev_val, raw)
                    ),
                    None,
                )
                if conflicting is not None:
                    out.append(
                        Contradiction(
                            key=topic,
                            description=f"sources disagree: '{conflicting.quote[:60]}' vs '{ev.quote[:60]}'",
                            evidence_id_a=conflicting.evidence_id,
                            evidence_id_b=ev.evidence_id,
                            severity=0.7,
                        )
                    )
                seen.append((raw, ev))
                if len(seen) > 40:
                    break
        return out[:5]


def _values_conflict(a: str, b: str) -> bool:
    """Same unit/shape but different magnitude ⇒ conflict. Heuristic, bounded."""
    ma, mb = _NUMBER_RE.match(a), _NUMBER_RE.match(b)
    if not ma or not mb:
        return False
    unit_a, unit_b = (ma.group(2) or "").lower(), (mb.group(2) or "").lower()
    if unit_a != unit_b:
        return False
    try:
        va, vb = float(ma.group(1)), float(mb.group(1))
    except ValueError:
        return False
    if va == 0 or vb == 0:
        return False
    ratio = max(va, vb) / max(min(va, vb), 1e-9)
    return ratio >= 1.5  # ≥50% apart with same unit = disagreement worth surfacing


class ClaimExtractor:
    """Deterministic claim extraction: factual-looking sentences (§32)."""

    def extract(self, text: str) -> list[Claim]:
        claims: list[Claim] = []
        for s in _SENTENCE_SPLIT.split(text):
            s = s.strip()
            if len(s) < 25 or len(s) > 500:
                continue
            low = s.lower()
            factual = bool(_NUMBER_RE.search(s)) or any(
                c in low for c in (" is ", " are ", " was ", " were ", " requires ", " supports ")
            )
            if not factual:
                continue
            cid = f"claim_{hashlib.sha256(s.encode()).hexdigest()[:16]}"
            claims.append(Claim(claim_id=cid, text=s, confidence=0.0))
            if len(claims) >= 12:
                break
        return claims


class ClaimVerifier:
    """Ground claims against evidence by token overlap (§33).

    SUPPORTED when the claim's content words largely appear in some evidence
    quote; CONTRADICTED when a contradiction pair spans the claim's values;
    else UNSUPPORTED — unsupported claims get removed/qualified (§124).
    """

    def __init__(self, *, support_threshold: float = 0.5) -> None:
        self._threshold = support_threshold

    def verify(self, claims: list[Claim], evidence: list[Evidence], contradictions: list[Contradiction]) -> list[Claim]:
        ev_tokens = [set(tokenize(ev.quote)) for ev in evidence]
        contradiction_ev_ids = {c.evidence_id_a for c in contradictions} | {c.evidence_id_b for c in contradictions}
        verified: list[Claim] = []
        for claim in claims:
            c_tokens = set(tokenize(claim.text)) - _STOPWORDS
            if not c_tokens:
                verified.append(claim.model_copy(update={"status": ClaimStatus.UNSUPPORTED}))
                continue
            best = 0.0
            best_ev: Evidence | None = None
            for toks, ev in zip(ev_tokens, evidence, strict=False):
                overlap = len(c_tokens & toks) / len(c_tokens)
                if overlap > best:
                    best = overlap
                    best_ev = ev
            if best >= self._threshold and best_ev is not None:
                status = ClaimStatus.DISPUTED if best_ev.evidence_id in contradiction_ev_ids else ClaimStatus.SUPPORTED
                verified.append(
                    claim.model_copy(
                        update={
                            "status": status,
                            "confidence": round(best, 3),
                            "evidence_ids": (best_ev.evidence_id,),
                        }
                    )
                )
            else:
                verified.append(
                    claim.model_copy(update={"status": ClaimStatus.UNSUPPORTED, "confidence": round(best, 3)})
                )
        return verified


_STOPWORDS = frozenset(
    """a an the is are was were of to in on for and or with that this it as be by at from has have had not no""".split()
)
