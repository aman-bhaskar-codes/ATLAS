"""Perception cache (Phase 20).

Perception is expensive (AX walks, DOM serialization, screenshots). Cache
snapshots briefly and invalidate on ANY mutating action — never use stale
perception for consequential actions.

KEY is (substrate, surface): one entry per surface. Bounded LRU by count and
TTL by age. The engine calls invalidate() after click/type/navigate/launch.
"""

from __future__ import annotations

import time
from collections import OrderedDict

from atlas.perception.contracts import PerceptionSnapshot, Substrate

_DEFAULT_TTL_S = 5.0
_DEFAULT_MAX_ENTRIES = 16

# Operations that mutate the surface and therefore invalidate perception.
MUTATING_OPERATIONS: frozenset[str] = frozenset(
    {
        "click",
        "type",
        "press",
        "scroll",
        "select",
        "drag",
        "submit",
        "navigate",
        "back",
        "forward",
        "reload",
        "launch",
        "close",
        "switch_window",
        "tap",
        "swipe",
        "long_press",
        "text",
    }
)


class PerceptionCache:
    def __init__(self, *, ttl_s: float = _DEFAULT_TTL_S, max_entries: int = _DEFAULT_MAX_ENTRIES) -> None:
        self._ttl_s = ttl_s
        self._max = max_entries
        self._entries: OrderedDict[tuple[Substrate, str], tuple[float, PerceptionSnapshot]] = OrderedDict()

    def get(self, substrate: Substrate, surface: str) -> PerceptionSnapshot | None:
        key = (substrate, surface)
        entry = self._entries.get(key)
        if entry is None:
            return None
        stored_at, snap = entry
        if (time.monotonic() - stored_at) > self._ttl_s:
            del self._entries[key]
            return None
        self._entries.move_to_end(key)
        return snap

    def put(self, substrate: Substrate, surface: str, snapshot: PerceptionSnapshot) -> None:
        key = (substrate, surface)
        self._entries[key] = (time.monotonic(), snapshot)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max:
            self._entries.popitem(last=False)

    def invalidate(self, substrate: Substrate | None = None) -> None:
        """Drop cached perception. substrate=None drops everything."""
        if substrate is None:
            self._entries.clear()
            return
        self._entries = OrderedDict((k, v) for k, v in self._entries.items() if k[0] != substrate)

    def invalidate_if_mutating(self, substrate: Substrate, operation: str) -> bool:
        """Invalidate when the operation mutates the surface. Returns True if dropped."""
        if operation in MUTATING_OPERATIONS:
            self.invalidate(substrate)
            return True
        return False

    def __len__(self) -> int:
        return len(self._entries)
