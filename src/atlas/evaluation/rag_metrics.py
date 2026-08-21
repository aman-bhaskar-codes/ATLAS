"""Ragas-style RAG metrics, ATLAS-native and deterministic (§59-63, §100).

No LLM graders: every metric is token-overlap based, reproducible, free,
and fast enough to run over the whole dataset on every experiment. The
metric NAMES map to Ragas so results are comparable; the implementations
are ours (§140: don't pretend to run Ragas itself).

- faithfulness        — answer claims grounded in retrieved context
- answer_relevancy    — answer stays on the question
- context_precision   — retrieved context relevant to the question
- context_recall      — retrieved context covers the ground truth
"""

from __future__ import annotations

import re

from atlas.knowledge.domain import Claim

_STOPWORDS = frozenset(
    "a an and are as at be but by for from has have how in is it its of on or that the this to was"
    " what when where which who will with".split()
)


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9_]+", text.lower()) if t not in _STOPWORDS and len(t) > 2}


def faithfulness(claims: list[Claim]) -> float:
    """Fraction of extracted claims that evidence SUPPORTS (§60)."""
    if not claims:
        return 1.0
    supported = sum(1 for c in claims if c.status.value == "SUPPORTED")
    return round(supported / len(claims), 3)


def answer_relevancy(answer: str, query: str) -> float:
    """Token overlap between question and answer — drift detector (§61)."""
    q, a = _tokens(query), _tokens(answer)
    if not q or not a:
        return 0.0
    return round(len(q & a) / len(q), 3)


def context_precision(query: str, contexts: list[str]) -> float:
    """Mean relevance of retrieved contexts to the question (§62)."""
    q = _tokens(query)
    if not q or not contexts:
        return 0.0
    scores = []
    for ctx in contexts:
        c = _tokens(ctx[:800])
        scores.append(len(q & c) / len(q) if c else 0.0)
    return round(sum(scores) / len(scores), 3)


def context_recall(ground_truth: str, contexts: list[str]) -> float:
    """How much of the ground-truth content the contexts cover (§63)."""
    gt = _tokens(ground_truth)
    if not gt:
        return 1.0
    if not contexts:
        return 0.0
    covered = set[str]()
    for ctx in contexts:
        covered |= _tokens(ctx[:2000])
    return round(len(gt & covered) / len(gt), 3)


def evaluate_answer(
    *,
    answer: str,
    query: str,
    contexts: list[str],
    claims: list[Claim],
    ground_truth: str = "",
) -> dict[str, float]:
    """One metrics row per answer — what experiments average over."""
    row = {
        "faithfulness": faithfulness(claims),
        "answer_relevancy": answer_relevancy(answer, query),
        "context_precision": context_precision(query, contexts),
    }
    if ground_truth:
        row["context_recall"] = context_recall(ground_truth, contexts)
    return row
