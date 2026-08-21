"""Feature reranker — authority, freshness, diversity on top of relevance (§26-29).

Deliberately NOT a model call: deterministic, cheap, explainable, and its
weights are the thing A/B experiments tune (§128). A learned reranker can
later replace it behind the same interface (training/ registry).
"""

from __future__ import annotations

from dataclasses import dataclass

from atlas.knowledge.bm25 import tokenize
from atlas.knowledge.retrieval import Candidate


@dataclass(frozen=True)
class RerankWeights:
    relevance: float = 0.55
    authority: float = 0.2
    freshness: float = 0.15
    overlap: float = 0.1
    diversity_penalty: float = 0.35  # MMR-style penalty for same-document repeats


class FeatureReranker:
    """score = w·features, then greedy MMR pass for diversity."""

    def __init__(self, weights: RerankWeights | None = None) -> None:
        self.weights = weights or RerankWeights()

    def rerank(self, query: str, candidates: list[Candidate], *, k: int = 20) -> list[Candidate]:
        if not candidates:
            return []
        w = self.weights
        q_tokens = set(tokenize(query))
        max_rrf = max(c.rrf_score for c in candidates) or 1.0

        scored: list[tuple[float, Candidate]] = []
        for c in candidates:
            relevance = c.rrf_score / max_rrf
            c_tokens = set(tokenize(c.chunk.content))
            overlap = len(q_tokens & c_tokens) / max(len(q_tokens), 1)
            score = (
                w.relevance * relevance
                + w.authority * c.document.authority
                + w.freshness * c.document.freshness
                + w.overlap * overlap
            )
            scored.append((score, c))
        scored.sort(key=lambda t: t[0], reverse=True)

        # Greedy MMR: penalize candidates from documents already selected.
        selected: list[tuple[float, Candidate]] = []
        doc_counts: dict[str, int] = {}
        for score, cand in scored:
            if len(selected) >= k:
                break
            n = doc_counts.get(cand.document.document_id, 0)
            penalized = score - w.diversity_penalty * min(n, 2) * (score if n else 0.0)
            selected.append((max(penalized, score * 0.25), cand))
            doc_counts[cand.document.document_id] = n + 1
        selected.sort(key=lambda t: t[0], reverse=True)
        return [c for _, c in selected]
