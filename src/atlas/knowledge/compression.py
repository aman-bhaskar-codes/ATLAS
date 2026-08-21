"""Citation-preserving compression (§122-123).

A summary may only enter trusted semantic memory if every retained sentence
still carries its provenance. So compression here is deterministic and
extractive: keep the highest-value sentences of an answer, preserve their
[n] markers verbatim, and report `provenance_complete=False` the moment a
kept sentence has no citation — such summaries are flagged, never trusted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_MARKER_RE = re.compile(r"\[(\d+)\]")


@dataclass(frozen=True)
class CompressedSummary:
    text: str
    kept_sentences: int
    total_sentences: int
    kept_markers: tuple[int, ...]  # citation indices that survived
    provenance_complete: bool  # False ⇒ must NOT enter trusted semantic memory


class CitationPreservingCompressor:
    def compress(self, text: str, *, max_chars: int = 1200, query: str = "") -> CompressedSummary:
        sentences = [s.strip() for s in _SENTENCE_RE.split(text) if s.strip()]
        if not sentences:
            return CompressedSummary(
                text="", kept_sentences=0, total_sentences=0, kept_markers=(), provenance_complete=True
            )

        q_tokens = set(re.findall(r"[a-z0-9_]+", query.lower()))
        scored: list[tuple[float, int, str]] = []
        for i, s in enumerate(sentences):
            has_marker = 1.0 if _MARKER_RE.search(s) else 0.0
            s_tokens = set(re.findall(r"[a-z0-9_]+", s.lower()))
            overlap = len(q_tokens & s_tokens) / max(len(q_tokens), 1) if q_tokens else 0.0
            # cited sentences always outrank uncited ones; position breaks ties early-first
            score = has_marker * 2.0 + overlap - i * 0.001
            scored.append((score, i, s))

        kept: list[tuple[int, str]] = []
        used = 0
        for _, i, s in sorted(scored, key=lambda t: (-t[0], t[1])):
            if used + len(s) + 1 > max_chars and kept:
                continue
            kept.append((i, s))
            used += len(s) + 1
            if used >= max_chars:
                break
        kept.sort(key=lambda t: t[0])  # restore original order
        compressed = " ".join(s for _, s in kept)

        markers = sorted({int(m) for _, s in kept for m in _MARKER_RE.findall(s)})
        complete = all(_MARKER_RE.search(s) for _, s in kept)
        return CompressedSummary(
            text=compressed,
            kept_sentences=len(kept),
            total_sentences=len(sentences),
            kept_markers=tuple(markers),
            provenance_complete=complete,
        )
