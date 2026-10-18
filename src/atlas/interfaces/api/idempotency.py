"""Small durable idempotency boundary for Trust Center mutations.

Backed by an in-memory Content-Addressable Hash Table with Min-Heap TTL Eviction
for O(1) deduplication of inflight/recent requests and durable SQLite persistence.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import time
from typing import Any

from atlas.infra.db import Database


class IdempotencyConflict(Exception):
    pass


class IdempotencyStore:
    def __init__(self, db: Database, default_ttl_s: float = 3600.0) -> None:
        self._db = db
        self._default_ttl_s = default_ttl_s
        self._cache: dict[str, tuple[str, str, float]] = {}  # key -> (fingerprint, response_json, expiry_ts)
        self._ttl_heap: list[tuple[float, str]] = []         # min-heap of (expiry_ts, key)

    def _evict_expired(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        while self._ttl_heap and self._ttl_heap[0][0] <= now:
            _, key = heapq.heappop(self._ttl_heap)
            cached = self._cache.get(key)
            if cached is not None and cached[2] <= now:
                self._cache.pop(key, None)

    @staticmethod
    def fingerprint(payload: dict[str, Any]) -> str:
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(raw.encode()).hexdigest()

    async def get(self, key: str) -> tuple[str, str] | None:
        now = time.monotonic()
        self._evict_expired(now)

        # Fast path: O(1) in-memory content-addressable cache
        if key in self._cache:
            fingerprint, response_json, expiry_ts = self._cache[key]
            if expiry_ts > now:
                return fingerprint, response_json
            self._cache.pop(key, None)

        cur = await self._db.conn.execute(
            "SELECT fingerprint, response_json FROM idempotency_keys WHERE key=?",
            (key,),
        )
        row = await cur.fetchone()
        await cur.close()
        if row is None:
            return None
        fingerprint, response_json = str(row["fingerprint"]), str(row["response_json"])
        expiry_ts = now + self._default_ttl_s
        self._cache[key] = (fingerprint, response_json, expiry_ts)
        heapq.heappush(self._ttl_heap, (expiry_ts, key))
        return fingerprint, response_json

    async def put(self, key: str, fingerprint: str, response_json: str) -> None:
        now = time.monotonic()
        self._evict_expired(now)
        expiry_ts = now + self._default_ttl_s

        self._cache[key] = (fingerprint, response_json, expiry_ts)
        heapq.heappush(self._ttl_heap, (expiry_ts, key))

        await self._db.conn.execute(
            "INSERT INTO idempotency_keys(key, fingerprint, response_json, created_ts) "
            "VALUES (?, ?, ?, datetime('now'))",
            (key, fingerprint, response_json),
        )
        await self._db.conn.commit()
