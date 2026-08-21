"""BM25 — dependency-free lexical index (§15, §138 fallback).

WHY our own BM25: hybrid retrieval needs a lexical leg that works offline,
for free, with zero new dependencies. BM25Okapi over lowercase word tokens
is enough for exact-term recall and is the designated fallback when the
embedding model or vector store is unavailable.
"""

from __future__ import annotations

import math
import re

_TOKEN = re.compile(r"[a-z0-9_]+")

# Query-side stopwords: matching on "about/the/of" alone fabricates relevance
# and defeats honest refusal (§133). Documents keep full tokenization.
_QUERY_STOPWORDS = frozenset(
    """a an the is are was were of to in on for and or with that this it as be by at from
    has have had not no do does did how what when where why which who whom me my mine your
    yours our ours tell about into over under after before please can could would should
    will shall may might some any all there here""".split()
)


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


class BM25Index:
    """In-memory BM25 over (id, text) items. Rebuilt from SQL on startup."""

    def __init__(self, *, k1: float = 1.5, b: float = 0.75) -> None:
        self._k1 = k1
        self._b = b
        self._docs: dict[str, list[str]] = {}
        self._df: dict[str, int] = {}
        self._avg_len = 0.0

    def build(self, items: list[tuple[str, str]]) -> None:
        self._docs = {}
        self._df = {}
        total = 0
        for ref, text in items:
            toks = tokenize(text)
            if not toks:
                continue
            self._docs[ref] = toks
            total += len(toks)
            for term in set(toks):
                self._df[term] = self._df.get(term, 0) + 1
        self._avg_len = total / len(self._docs) if self._docs else 0.0

    def add(self, ref: str, text: str) -> None:
        """Incremental add (recomputes avg length; cheap for single docs)."""
        toks = tokenize(text)
        if not toks:
            return
        if ref in self._docs:
            self.remove(ref)
        self._docs[ref] = toks
        for term in set(toks):
            self._df[term] = self._df.get(term, 0) + 1
        total = self._avg_len * (len(self._docs) - 1) + len(toks)
        self._avg_len = total / len(self._docs)

    def remove(self, ref: str) -> None:
        toks = self._docs.pop(ref, None)
        if toks is None:
            return
        for term in set(toks):
            self._df[term] = max(0, self._df.get(term, 1) - 1)
        if self._docs:
            self._avg_len = sum(len(t) for t in self._docs.values()) / len(self._docs)
        else:
            self._avg_len = 0.0

    @property
    def size(self) -> int:
        return len(self._docs)

    def query(self, text: str, k: int = 20) -> list[tuple[str, float]]:
        """Return top-k (ref, score). Empty corpus/query → []."""
        q_toks = [t for t in tokenize(text) if t not in _QUERY_STOPWORDS]
        if not q_toks or not self._docs:
            return []
        n = len(self._docs)
        scores: dict[str, float] = {}
        for term in set(q_toks):
            df = self._df.get(term, 0)
            if df == 0:
                continue
            idf = math.log(1.0 + (n - df + 0.5) / (df + 0.5))
            for ref, toks in self._docs.items():
                tf = toks.count(term)
                if tf == 0:
                    continue
                denom = tf + self._k1 * (1.0 - self._b + self._b * len(toks) / max(self._avg_len, 1.0))
                scores[ref] = scores.get(ref, 0.0) + idf * (tf * (self._k1 + 1.0)) / denom
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        return ranked[:k]
