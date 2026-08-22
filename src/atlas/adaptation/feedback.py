"""Domain feedback loops — tools, sources, memory, human feedback, user
corrections (Prompt 4 §57-§62).

Each loop records measured observations and adapts conservatively. Nothing
here may rewrite platform adapters from a single failure (§56) or convert
one correction into universal policy (§62).
"""

from __future__ import annotations

from atlas.adaptation.domain import (
    HumanFeedbackRecord,
    MemoryFeedbackRecord,
    SourceTrustRecord,
    ToolPerformanceRecord,
    UserCorrection,
)
from atlas.infra.clock import Clock, SystemClock
from atlas.infra.db import Database
from atlas.infra.logging import get_logger

_log = get_logger("atlas.adaptation.feedback")

#: §58: exponential moving average weight for trust updates. Small — trust
#: must move slowly and frequency is never equated with truth.
SOURCE_TRUST_ALPHA = 0.1

#: §62: corrections below this count are a preference signal, not policy.
CORRECTION_POLICY_THRESHOLD = 5


class ToolPerformanceStore:
    """§57: tracks tool × task-class success, latency, failure, recovery."""

    def __init__(self, *, db: Database) -> None:
        self._db = db

    async def record(self, record: ToolPerformanceRecord) -> None:
        await self._db.conn.execute(
            """
            INSERT INTO tool_performance (
                tool_name, task_class, success, latency_ms, failure_reason,
                recovered, created_ts
            ) VALUES (?,?,?,?,?,?,?)
            """,
            (
                record.tool_name,
                record.task_class,
                int(record.success),
                record.latency_ms,
                record.failure_reason,
                int(record.recovered),
                record.created_ts,
            ),
        )
        await self._db.conn.commit()

    async def reliability(self, task_class: str) -> dict[str, tuple[float, float, int]]:
        """Per-tool (success_rate, avg_latency_ms, n) for one task class —
        the routing evidence for 'tool A is more reliable than tool B'."""
        cur = await self._db.conn.execute(
            """
            SELECT tool_name,
                   AVG(success) AS success_rate,
                   AVG(latency_ms) AS avg_latency,
                   COUNT(*) AS n
            FROM tool_performance WHERE task_class=? GROUP BY tool_name
            """,
            (task_class,),
        )
        rows = await cur.fetchall()
        return {str(r["tool_name"]): (float(r["success_rate"]), float(r["avg_latency"]), int(r["n"])) for r in rows}

    async def best_tool(self, task_class: str, *, min_samples: int = 3) -> str | None:
        stats = await self.reliability(task_class)
        eligible = {t: s for t, s in stats.items() if s[2] >= min_samples}
        if not eligible:
            return None
        return max(eligible, key=lambda t: (eligible[t][0], -eligible[t][1]))


class SourceTrustLearner:
    """§58: conservative source-trust adaptation connected to the Knowledge
    Fabric. Contradictions lower trust; repeated frequency never raises it
    by itself."""

    def __init__(self, *, db: Database, clock: Clock | None = None) -> None:
        self._db = db
        self._clock = clock or SystemClock()

    async def observe(
        self,
        source: str,
        *,
        useful: bool | None = None,
        claim_correct: bool | None = None,
        citation_accepted: bool | None = None,
        fresh: bool | None = None,
        contradicted: bool = False,
    ) -> SourceTrustRecord:
        existing = await self.get(source)
        alpha = SOURCE_TRUST_ALPHA

        def move(current: float, signal: bool | None) -> float:
            if signal is None:
                return current
            return current + alpha * ((1.0 if signal else 0.0) - current)

        usefulness = move(existing.usefulness if existing else 0.5, useful)
        correctness = move(existing.claim_correctness if existing else 0.5, claim_correct)
        acceptance = move(existing.citation_acceptance if existing else 0.5, citation_accepted)
        freshness = move(existing.freshness_score if existing else 0.5, fresh)
        n = (existing.n_observations if existing else 0) + 1
        contradiction_rate = (
            (existing.contradiction_rate * (n - 1) + (1.0 if contradicted else 0.0)) / n if n else 0.0
        )
        # Trust: quality signals raise it slowly; contradictions pull it down.
        quality = (usefulness + correctness + acceptance + freshness) / 4
        trust = (existing.trust if existing else 0.5) + alpha * (quality - 0.5)
        if contradicted:
            trust = max(0.0, trust - 0.05)
        trust = min(1.0, max(0.0, trust))
        record = SourceTrustRecord(
            source=source,
            usefulness=usefulness,
            claim_correctness=correctness,
            citation_acceptance=acceptance,
            freshness_score=freshness,
            contradiction_rate=contradiction_rate,
            trust=trust,
            n_observations=n,
            updated_ts=self._clock.now().isoformat(),
        )
        await self._db.conn.execute(
            """
            INSERT OR REPLACE INTO source_trust (
                source, usefulness, claim_correctness, citation_acceptance,
                freshness_score, contradiction_rate, trust, n_observations, updated_ts
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                record.source,
                record.usefulness,
                record.claim_correctness,
                record.citation_acceptance,
                record.freshness_score,
                record.contradiction_rate,
                record.trust,
                record.n_observations,
                record.updated_ts,
            ),
        )
        await self._db.conn.commit()
        return record

    async def get(self, source: str) -> SourceTrustRecord | None:
        cur = await self._db.conn.execute("SELECT * FROM source_trust WHERE source=?", (source,))
        row = await cur.fetchone()
        if row is None:
            return None
        d = dict(row)
        return SourceTrustRecord(
            source=str(d["source"]),
            usefulness=float(d["usefulness"]),
            claim_correctness=float(d["claim_correctness"]),
            citation_acceptance=float(d["citation_acceptance"]),
            freshness_score=float(d["freshness_score"]),
            contradiction_rate=float(d["contradiction_rate"]),
            trust=float(d["trust"]),
            n_observations=int(d["n_observations"]),
            updated_ts=str(d["updated_ts"]),
        )


class MemoryFeedbackStore:
    """§59: did retrieved memory help, distract or carry stale information?
    Feeds ranking/importance/retention — never raw memory volume."""

    def __init__(self, *, db: Database) -> None:
        self._db = db

    async def record(self, feedback: MemoryFeedbackRecord) -> None:
        await self._db.conn.execute(
            """
            INSERT INTO memory_feedback (
                memory_id, task_id, helped, distracted, stale, rating, created_ts
            ) VALUES (?,?,?,?,?,?,?)
            """,
            (
                feedback.memory_id,
                feedback.task_id,
                int(feedback.helped),
                int(feedback.distracted),
                int(feedback.stale),
                feedback.rating,
                feedback.created_ts,
            ),
        )
        await self._db.conn.commit()

    async def usefulness(self, memory_id: str) -> tuple[float, int]:
        """(help-rate − distract-rate, observations) for one memory."""
        cur = await self._db.conn.execute(
            "SELECT AVG(helped) AS h, AVG(distracted) AS d, COUNT(*) AS n FROM memory_feedback WHERE memory_id=?",
            (memory_id,),
        )
        row = await cur.fetchone()
        if row is None or int(row["n"]) == 0:
            return 0.0, 0
        return float(row["h"]) - float(row["d"]), int(row["n"])


class HumanFeedbackStore:
    """§61: human feedback with explicit reliability weights."""

    def __init__(self, *, db: Database) -> None:
        self._db = db

    async def record(self, feedback: HumanFeedbackRecord) -> None:
        if not 0.0 <= feedback.reliability <= 1.0:
            msg = f"reliability must be within [0,1], got {feedback.reliability}"
            raise ValueError(msg)
        await self._db.conn.execute(
            """
            INSERT INTO human_feedback (kind, ref_kind, ref_id, content, reliability, created_ts)
            VALUES (?,?,?,?,?,?)
            """,
            (
                feedback.kind.value,
                feedback.ref_kind,
                feedback.ref_id,
                feedback.content,
                feedback.reliability,
                feedback.created_ts,
            ),
        )
        await self._db.conn.commit()

    async def weighted_sentiment(self, ref_kind: str, ref_id: str) -> float:
        """Reliability-weighted thumbs balance in [-1, 1] — thumbs_up is +1,
        thumbs_down is -1, other kinds are neutral evidence."""
        cur = await self._db.conn.execute(
            "SELECT kind, reliability FROM human_feedback WHERE ref_kind=? AND ref_id=?",
            (ref_kind, ref_id),
        )
        rows = await cur.fetchall()
        score = 0.0
        total = 0.0
        for r in rows:
            weight = float(r["reliability"])
            if str(r["kind"]) == "thumbs_up":
                score += weight
            elif str(r["kind"]) == "thumbs_down":
                score -= weight
            total += weight
        return score / total if total > 0 else 0.0


class UserCorrectionStore:
    """§62: user corrections accumulate as preference evidence. Below the
    policy threshold they stay a preference — never universal policy."""

    def __init__(self, *, db: Database) -> None:
        self._db = db

    async def record(self, correction: UserCorrection) -> None:
        await self._db.conn.execute(
            "INSERT INTO user_corrections (task_class, preferred_source_strategy, context, count, created_ts) VALUES (?,?,?,?,?)",
            (
                correction.task_class,
                correction.preferred_source_strategy,
                correction.context,
                correction.count,
                correction.created_ts,
            ),
        )
        await self._db.conn.commit()
        _log.info(
            "feedback.user_correction",
            event_type="adaptation",
            task_class=correction.task_class,
            strategy=correction.preferred_source_strategy,
        )

    async def preference_strength(self, task_class: str, strategy: str) -> int:
        cur = await self._db.conn.execute(
            "SELECT COALESCE(SUM(count), 0) AS total FROM user_corrections WHERE task_class=? AND preferred_source_strategy=?",
            (task_class, strategy),
        )
        row = await cur.fetchone()
        return int(row["total"]) if row is not None else 0

    def is_policy_level(self, task_class: str, count: int) -> bool:
        """§62: one correction never becomes universal policy."""
        del task_class
        return count >= CORRECTION_POLICY_THRESHOLD


__all__ = [
    "CORRECTION_POLICY_THRESHOLD",
    "SOURCE_TRUST_ALPHA",
    "HumanFeedbackStore",
    "MemoryFeedbackStore",
    "SourceTrustLearner",
    "ToolPerformanceStore",
    "UserCorrectionStore",
]
