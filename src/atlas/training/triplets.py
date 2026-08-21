"""Feedback → training triplets (§67-69).

User feedback labels (§125) are the only supervision signal: pairs marked
correct/used-in-answer become POSITIVES for their query; incorrect /
wrong_source pairs become NEGATIVES. Queries with only positives get one
hard negative mined from the corpus — the least overlapping chunk of a
different document — so contrastive training never lacks negatives.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

_POSITIVE_LABELS = frozenset({"correct"})
_NEGATIVE_LABELS = frozenset({"incorrect", "wrong_source", "outdated"})

_STOPWORDS = frozenset(
    "a an and are as at be but by for from has have how in is it its of on or that the this to was"
    " what when where which who will with".split()
)


class ChunkResolver(Protocol):
    async def get_chunk(self, chunk_id: str) -> tuple[Any, Any] | None: ...


@dataclass(frozen=True)
class Triplet:
    anchor: str  # query
    positive: str  # chunk content the user validated
    negative: str  # chunk content rejected or off-topic
    hard: bool = False  # negative was mined, not user-labelled


@dataclass(frozen=True)
class TripletReport:
    triplets: tuple[Triplet, ...]
    pairs_seen: int
    skipped_no_content: int
    hard_negatives_added: int


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9_]+", text.lower()) if t not in _STOPWORDS and len(t) > 2}


async def mine_triplets(
    pairs: list[dict[str, Any]], resolver: ChunkResolver, *, max_triplets: int = 500
) -> TripletReport:
    by_query: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for p in pairs:
        label = str(p.get("label", ""))
        if label not in _POSITIVE_LABELS and label not in _NEGATIVE_LABELS:
            continue
        bucket = by_query.setdefault(str(p.get("query", "")), {"pos": [], "neg": []})
        bucket["pos" if label in _POSITIVE_LABELS else "neg"].append(p)

    # Corpus pool for hard negatives: every chunk seen in feedback.
    pool: list[str] = []
    cache: dict[str, str] = {}

    async def content_of(chunk_id: str) -> str:
        if chunk_id in cache:
            return cache[chunk_id]
        found = await resolver.get_chunk(chunk_id)
        text = found[0].content if found else ""
        cache[chunk_id] = text
        return text

    for bucket in by_query.values():
        for p in bucket["pos"] + bucket["neg"]:
            text = await content_of(str(p.get("chunk_id", "")))
            if text and text not in pool:
                pool.append(text)

    triplets: list[Triplet] = []
    skipped = 0
    hard_added = 0
    for query, bucket in by_query.items():
        if not query:
            continue
        positives = [await content_of(str(p.get("chunk_id", ""))) for p in bucket["pos"]]
        positives = [t for t in positives if t]
        negatives = [await content_of(str(p.get("chunk_id", ""))) for p in bucket["neg"]]
        negatives = [t for t in negatives if t]

        if not positives:
            skipped += len(bucket["neg"])
            continue
        if not negatives:
            mined = _hard_negative(query, positives, pool)
            if mined:
                negatives = [mined]
                hard_added += 1
            else:
                skipped += len(bucket["pos"])
                continue
        for pos in positives:
            for neg in negatives:
                triplets.append(Triplet(anchor=query, positive=pos, negative=neg, hard=not bucket["neg"]))
                if len(triplets) >= max_triplets:
                    return TripletReport(
                        triplets=tuple(triplets),
                        pairs_seen=len(pairs),
                        skipped_no_content=skipped,
                        hard_negatives_added=hard_added,
                    )
    return TripletReport(
        triplets=tuple(triplets), pairs_seen=len(pairs), skipped_no_content=skipped, hard_negatives_added=hard_added
    )


def _hard_negative(query: str, positives: list[str], pool: list[str]) -> str:
    """Least query-overlapping, non-positive corpus chunk (§69)."""
    q_tokens = _tokens(query)
    pos_set = set(positives)
    best, best_score = "", 2.0
    for text in pool:
        if text in pos_set:
            continue
        overlap = len(q_tokens & _tokens(text[:800])) / max(len(q_tokens), 1)
        if overlap < best_score:
            best, best_score = text, overlap
    return best
