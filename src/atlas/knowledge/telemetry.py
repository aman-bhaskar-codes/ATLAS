"""RAG telemetry — every fabric query leaves a machine-readable record (§98, §58).

WHY record everything: retrieval failures must be diagnosable later ("why did
retrieval fail?" §126). Each query records mode, route, stage latencies,
candidate/evidence counts, and — when it fails or degrades — a FailureCause
code the evaluation layer can mine. Ring buffer in memory; optional durable
persist into rag_records.
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from atlas.knowledge.domain import FailureCause, QueryRoute, RAGMode

if TYPE_CHECKING:
    from atlas.infra.db import Database


@dataclass(frozen=True)
class RagRecord:
    record_id: str
    query: str
    mode: RAGMode
    route: QueryRoute
    created_ts: datetime
    latency_ms: int = 0
    retrieve_ms: int = 0
    rerank_ms: int = 0
    synthesize_ms: int = 0
    candidate_count: int = 0
    evidence_count: int = 0
    answered: bool = False
    degraded: bool = False
    failure: FailureCause | None = None
    detail: dict[str, Any] = field(default_factory=dict)


class RagTelemetry:
    """Ring buffer of RagRecords with cheap aggregate summaries."""

    def __init__(self, maxlen: int = 512) -> None:
        self._records: deque[RagRecord] = deque(maxlen=maxlen)
        self._db: Database | None = None

    def attach_db(self, db: Database) -> None:
        self._db = db

    def record(self, rec: RagRecord) -> None:
        self._records.append(rec)

    @property
    def records(self) -> list[RagRecord]:
        return list(self._records)

    def failures(self) -> list[RagRecord]:
        return [r for r in self._records if r.failure is not None]

    def summary(self) -> dict[str, Any]:
        n = len(self._records)
        if n == 0:
            return {"count": 0}
        latencies = sorted(r.latency_ms for r in self._records)
        p95 = latencies[min(n - 1, int(n * 0.95))]
        by_failure: dict[str, int] = {}
        for r in self._records:
            if r.failure is not None:
                by_failure[r.failure.value] = by_failure.get(r.failure.value, 0) + 1
        return {
            "count": n,
            "answered": sum(1 for r in self._records if r.answered),
            "degraded": sum(1 for r in self._records if r.degraded),
            "answer_rate": round(sum(1 for r in self._records if r.answered) / n, 3),
            "p95_latency_ms": p95,
            "failures": by_failure,
        }

    async def persist(self, rec: RagRecord) -> None:
        """Best-effort durable write; telemetry must never break the hot path."""
        if self._db is None:
            return
        try:
            await self._db.conn.execute(
                """
                INSERT INTO rag_records
                (id, query, mode, route, latency_ms, retrieve_ms, rerank_ms,
                 synthesize_ms, candidate_count, evidence_count, answered,
                 degraded, failure, detail_json, created_ts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rec.record_id,
                    rec.query[:500],
                    rec.mode.value,
                    rec.route.value,
                    rec.latency_ms,
                    rec.retrieve_ms,
                    rec.rerank_ms,
                    rec.synthesize_ms,
                    rec.candidate_count,
                    rec.evidence_count,
                    1 if rec.answered else 0,
                    1 if rec.degraded else 0,
                    rec.failure.value if rec.failure else None,
                    json.dumps(rec.detail),
                    rec.created_ts.isoformat(),
                ),
            )
            await self._db.conn.commit()
        except Exception:
            pass  # telemetry is advisory
