"""In-process memory caches — sub-millisecond reads for hot paths.

WHY a dedicated cache module: caching logic scattered across EpisodicMemory,
SemanticMemory, and UserModel creates hidden state and subtle invalidation bugs.
One module owns the policy; the memory classes just call it.

Three caches:
  RetrievalCache    — full RetrievedContext keyed by (query_hash, task_id).
                      TTL 30 s, evict on any memory.stored / fact_added event.
                      Gives < 1 ms Observe step for repeated or similar queries.

  FactCache         — SemanticFact list keyed by (kind, min_confidence).
                      TTL 60 s. Invalidated when add_fact() or supersede() runs.
                      Avoids repeated DB reads for fact listing in the dashboard.

  StatsCache        — Aggregate counts (episodes / facts / docs / chunks).
                      TTL 10 s. Invalidated by any memory write.
                      Keeps /api/v1/memory/stats fast without hitting DB every poll.

All caches are thread-safe via asyncio.Lock.  They are *process-local* — no Redis
needed for single-user deployment.  The invalidation model is conservative (any
write flushes the cache) so correctness is always preserved.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any

# ---------------------------------------------------------------------------
# Generic TTL entry
# ---------------------------------------------------------------------------


class _Entry:
    __slots__ = ("expires_at", "value")

    def __init__(self, value: Any, ttl: float) -> None:
        self.value = value
        self.expires_at = time.monotonic() + ttl

    def is_live(self) -> bool:
        return time.monotonic() < self.expires_at


# ---------------------------------------------------------------------------
# Retrieval cache  (< 1 ms on HIT)
# ---------------------------------------------------------------------------


class RetrievalCache:
    """Cache full RetrievedContext by (query, task_id).

    Key design choices:
    - We hash the query string so key comparisons are O(1).
    - task_id is part of the key because per-task memory differs.
    - TTL is short (30 s) because memory is written frequently during execution.
    - Any memory write event triggers a full flush — correctness > hit-rate.
    """

    DEFAULT_TTL = 30.0  # seconds
    MAX_ENTRIES = 512  # prevent unbounded growth

    def __init__(self, ttl: float = DEFAULT_TTL) -> None:
        self._ttl = ttl
        self._store: dict[str, _Entry] = {}
        self._lock = asyncio.Lock()

    # ---- public API --------------------------------------------------------

    def make_key(self, query: str, task_id: str | None) -> str:
        raw = f"{query}|{task_id or ''}"
        return hashlib.md5(raw.encode()).hexdigest()

    async def get(self, key: str) -> Any | None:
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if not entry.is_live():
                del self._store[key]
                return None
            return entry.value

    async def set(self, key: str, value: Any) -> None:
        async with self._lock:
            # Evict expired entries when near capacity
            if len(self._store) >= self.MAX_ENTRIES:
                self._evict_expired()
                # If still full, drop the oldest 20 %
                if len(self._store) >= self.MAX_ENTRIES:
                    to_remove = list(self._store.keys())[: self.MAX_ENTRIES // 5]
                    for k in to_remove:
                        del self._store[k]
            self._store[key] = _Entry(value, self._ttl)

    async def invalidate(self) -> None:
        """Flush everything — called on any memory write."""
        async with self._lock:
            self._store.clear()

    # ---- private -----------------------------------------------------------

    def _evict_expired(self) -> None:
        dead = [k for k, e in self._store.items() if not e.is_live()]
        for k in dead:
            del self._store[k]

    @property
    def size(self) -> int:
        return len(self._store)


# ---------------------------------------------------------------------------
# Fact list cache  (< 1 ms on HIT)
# ---------------------------------------------------------------------------


class FactCache:
    """Cache SemanticFact lists by (kind, min_confidence).

    Much simpler than RetrievalCache because the keyspace is small (at most
    N_kinds x N_confidence_tiers ≈ 50 entries) and invalidation is coarse.
    """

    DEFAULT_TTL = 60.0
    MAX_ENTRIES = 256

    def __init__(self, ttl: float = DEFAULT_TTL) -> None:
        self._ttl = ttl
        self._store: dict[str, _Entry] = {}
        self._lock = asyncio.Lock()

    def make_key(self, kind: str | None, min_confidence: float, limit: int) -> str:
        return f"{kind}|{min_confidence:.3f}|{limit}"

    async def get(self, key: str) -> Any | None:
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if not entry.is_live():
                del self._store[key]
                return None
            return entry.value

    async def set(self, key: str, value: Any) -> None:
        async with self._lock:
            if len(self._store) >= self.MAX_ENTRIES:
                self._evict_expired()
            self._store[key] = _Entry(value, self._ttl)

    async def invalidate(self) -> None:
        async with self._lock:
            self._store.clear()

    def _evict_expired(self) -> None:
        dead = [k for k, e in self._store.items() if not e.is_live()]
        for k in dead:
            del self._store[k]

    @property
    def size(self) -> int:
        return len(self._store)


# ---------------------------------------------------------------------------
# Stats cache  (< 1 ms on HIT)
# ---------------------------------------------------------------------------


class StatsCache:
    """Single-entry cache for aggregate memory counts.

    The whole point is to avoid 4 COUNT(*) queries on every dashboard poll.
    TTL is 10 s so the dashboard stays nearly-live without hammering SQLite.
    """

    DEFAULT_TTL = 10.0

    def __init__(self, ttl: float = DEFAULT_TTL) -> None:
        self._ttl = ttl
        self._entry: _Entry | None = None
        self._lock = asyncio.Lock()

    async def get(self) -> dict[str, int] | None:
        async with self._lock:
            if self._entry is None or not self._entry.is_live():
                self._entry = None
                return None
            v = self._entry.value
            return v if isinstance(v, dict) else None

    async def set(self, value: dict[str, int]) -> None:
        async with self._lock:
            self._entry = _Entry(value, self._ttl)

    async def invalidate(self) -> None:
        async with self._lock:
            self._entry = None
