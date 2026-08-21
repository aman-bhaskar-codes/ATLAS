"""QueryResultCache — short-lived cache of ANSWERS, distinct from document caches (§50).

`ResearchCache` caches fetched content; this caches (query, mode) → FabricAnswer
so identical near-term questions are sub-millisecond. Invalidation: TTL only —
answers are cheap to regenerate, documents are not. Never cache refusals long.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from atlas.knowledge.synthesis import FabricAnswer


@dataclass(frozen=True)
class _Entry:
    answer: FabricAnswer
    stored_at: float


class QueryResultCache:
    def __init__(self, *, ttl_s: float = 60.0, max_entries: int = 64) -> None:
        self._ttl = ttl_s
        self._max = max_entries
        self._entries: dict[str, _Entry] = {}
        self.hits = 0
        self.misses = 0

    @staticmethod
    def make_key(query: str, mode: str) -> str:
        return f"{mode}:{' '.join(query.lower().split())}"

    def get(self, key: str) -> FabricAnswer | None:
        entry = self._entries.get(key)
        if entry is None:
            self.misses += 1
            return None
        if time.monotonic() - entry.stored_at > self._ttl:
            self._entries.pop(key, None)
            self.misses += 1
            return None
        # Refusals are always regenerated — cheap, and stale "no evidence" is harmful.
        if not entry.answer.answered:
            self.misses += 1
            return None
        self.hits += 1
        return entry.answer

    def put(self, key: str, answer: FabricAnswer) -> None:
        if len(self._entries) >= self._max:
            oldest = min(self._entries, key=lambda k: self._entries[k].stored_at)
            self._entries.pop(oldest, None)
        self._entries[key] = _Entry(answer=answer, stored_at=time.monotonic())

    def invalidate(self) -> None:
        self._entries.clear()
