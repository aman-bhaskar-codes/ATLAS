"""Lightweight in-process metrics.

WHY hand-rolled and not prometheus_client yet: Phase 1 has no scrape endpoint;
we only need cheap in-memory aggregation that a Phase-10 exporter can read.
Recording never raises into callers.
"""

from __future__ import annotations

from collections import defaultdict
from threading import Lock


class Metrics:
    def __init__(self) -> None:
        self._counters: dict[str, float] = defaultdict(float)
        self._gauges: dict[str, float] = {}
        self._hist: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()

    def counter(self, name: str, value: float = 1.0) -> None:
        with self._lock:
            self._counters[name] += value

    def gauge(self, name: str, value: float) -> None:
        with self._lock:
            self._gauges[name] = value

    def observe(self, name: str, value: float) -> None:
        with self._lock:
            self._hist[name].append(value)

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "histograms": {k: list(v) for k, v in self._hist.items()},
            }
