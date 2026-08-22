"""Research learning — stop optimization and counterfactual research
(Prompt 4 §63-§65).

§63-§64: ATLAS measures sources searched, unique information gained and
answer quality, and learns stopping points like 'after N strong independent
sources, further browsing rarely improves quality'. §65: bad research
outcomes are replayed against alternative query rewrites / source orders /
retrieval / verification strategies, and winners feed strategy evidence.
"""

from __future__ import annotations

from typing import Protocol

from atlas.adaptation.domain import ResearchSessionFeedback
from atlas.infra.clock import Clock, SystemClock
from atlas.infra.db import Database
from atlas.infra.logging import get_logger

_log = get_logger("atlas.adaptation.research")

#: §64: marginal information gain below this fraction is "rarely improves".
MARGINAL_GAIN_THRESHOLD = 0.05

#: §64: minimum observed sessions before a stop recommendation is made.
MIN_SESSIONS_FOR_STOP = 5


class ResearchVariantRunner(Protocol):
    """§65: executes one counterfactual research variant and returns its
    answer quality. Implementations decide how the variant differs
    (query rewrite, source order, retrieval strategy, verification)."""

    async def run_variant(self, task_id: str, variant: str) -> float: ...


class ResearchStopOptimizer:
    """§63-§64: persists research sessions and derives an evidence-based
    stop threshold. Returns None when evidence is insufficient — no guess."""

    def __init__(self, *, db: Database, clock: Clock | None = None) -> None:
        self._db = db
        self._clock = clock or SystemClock()

    async def record(self, session: ResearchSessionFeedback) -> None:
        await self._db.conn.execute(
            """
            INSERT INTO research_feedback (
                task_id, sources_searched, unique_information, answer_quality, created_ts
            ) VALUES (?,?,?,?,?)
            """,
            (
                session.task_id,
                session.sources_searched,
                session.unique_information,
                session.answer_quality,
                session.created_ts,
            ),
        )
        await self._db.conn.commit()

    async def sessions(self) -> tuple[ResearchSessionFeedback, ...]:
        cur = await self._db.conn.execute("SELECT * FROM research_feedback ORDER BY id")
        rows = await cur.fetchall()
        return tuple(
            ResearchSessionFeedback(
                task_id=str(d["task_id"]),
                sources_searched=int(d["sources_searched"]),
                unique_information=float(d["unique_information"]),
                answer_quality=float(d["answer_quality"]),
                created_ts=str(d["created_ts"]),
            )
            for row in rows
            if (d := dict(row))
        )

    async def recommended_stop(self) -> int | None:
        """Smallest N such that beyond N sources the marginal information
        gain is negligible while answer quality stays high. None = not
        enough evidence (§48)."""
        sessions = await self.sessions()
        if len(sessions) < MIN_SESSIONS_FOR_STOP:
            return None
        max_sources = max(s.sources_searched for s in sessions)
        if max_sources < 2:
            return None
        # Bucket information by source count; find where marginal gain dies.
        by_count: dict[int, list[ResearchSessionFeedback]] = {}
        for session in sessions:
            by_count.setdefault(session.sources_searched, []).append(session)
        counts = sorted(by_count)
        stop_at: int | None = None
        for i in range(1, len(counts)):
            lo, hi = counts[i - 1], counts[i]
            if hi - lo != 1:
                continue  # only judge adjacent source counts
            gain_lo = sum(s.unique_information for s in by_count[lo]) / len(by_count[lo])
            gain_hi = sum(s.unique_information for s in by_count[hi]) / len(by_count[hi])
            quality_hi = sum(s.answer_quality for s in by_count[hi]) / len(by_count[hi])
            if gain_hi - gain_lo < MARGINAL_GAIN_THRESHOLD and quality_hi >= 0.7:
                stop_at = lo
                break
        return stop_at


class CounterfactualResearch:
    """§65: for a bad research outcome, compare alternative strategies.
    Successful alternatives become strategy evidence — they are never
    applied directly from here."""

    def __init__(self, *, db: Database, runner: ResearchVariantRunner) -> None:
        self._db = db
        self._runner = runner

    async def compare(
        self,
        task_id: str,
        original_quality: float,
        variants: tuple[str, ...],
    ) -> tuple[tuple[str, float], ...]:
        """Returns variants beating the original outcome, best first."""
        if not variants:
            msg = "counterfactual research needs at least one variant (§48)"
            raise ValueError(msg)
        results: list[tuple[str, float]] = []
        for variant in variants:
            quality = await self._runner.run_variant(task_id, variant)
            results.append((variant, quality))
            _log.info(
                "research.counterfactual",
                event_type="adaptation",
                task_id=task_id,
                variant=variant,
                quality=round(quality, 3),
            )
        winners = sorted(
            ((v, q) for v, q in results if q > original_quality),
            key=lambda item: -item[1],
        )
        return tuple(winners)


__all__ = [
    "MARGINAL_GAIN_THRESHOLD",
    "MIN_SESSIONS_FOR_STOP",
    "CounterfactualResearch",
    "ResearchStopOptimizer",
    "ResearchVariantRunner",
]
