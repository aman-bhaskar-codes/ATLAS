"""Strategy memory — governed task-approach preferences.

A strategy records WHICH approach (model class, tool set, plan shape) worked
for a class of tasks. Strategies are advisory: they bias selection, they never
bypass safety or budgets. Promotion from candidate to active requires offline
evaluation evidence (eval_score), mirroring the A/B promotion model — never a
single live run.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from atlas.infra.clock import Clock
from atlas.infra.db import Database
from atlas.infra.ids import IdGenerator
from atlas.infra.logging import get_logger

_log = get_logger("atlas.memory.strategies")

ACTIVATION_MIN_EVIDENCE = 3
ACTIVATION_MIN_SUCCESS_RATE = 0.7


class StrategyStatus:
    CANDIDATE = "candidate"
    ACTIVE = "active"
    RETIRED = "retired"


class Strategy(BaseModel):
    model_config = {"frozen": True}

    id: str
    task_type_pattern: str  # e.g. "research.*", "coding.debug"
    approach: str  # human-readable description of the approach
    model_preference: str | None = None
    tool_preference: tuple[str, ...] = ()
    status: str = StrategyStatus.CANDIDATE
    success_rate: float = 0.0
    evidence_count: int = 0
    eval_score: float | None = None  # offline evaluation result (required to activate)
    created_ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_ts: datetime = Field(default_factory=lambda: datetime.now(UTC))


class StrategyStore:
    def __init__(self, db: Database, ids: IdGenerator, clock: Clock) -> None:
        self._db = db
        self._ids = ids
        self._clock = clock

    async def save(self, strategy: Strategy) -> str:
        await self._db.conn.execute(
            "INSERT OR REPLACE INTO strategies (id, task_type_pattern, approach, "
            "model_preference, tool_preference, status, success_rate, evidence_count, "
            "eval_score, created_ts, updated_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                strategy.id,
                strategy.task_type_pattern,
                strategy.approach,
                strategy.model_preference,
                json.dumps(list(strategy.tool_preference)),
                strategy.status,
                strategy.success_rate,
                strategy.evidence_count,
                strategy.eval_score,
                strategy.created_ts.isoformat(),
                self._clock.now().isoformat(),
            ),
        )
        await self._db.conn.commit()
        return strategy.id

    async def get(self, strategy_id: str) -> Strategy | None:
        cur = await self._db.conn.execute("SELECT * FROM strategies WHERE id = ?", (strategy_id,))
        row = await cur.fetchone()
        return self._from_row(row) if row else None

    async def active_for(self, task_type: str) -> list[Strategy]:
        """Active strategies matching a task type via fnmatch-style prefix."""
        import fnmatch

        cur = await self._db.conn.execute("SELECT * FROM strategies WHERE status = 'active'")
        rows = await cur.fetchall()
        out = []
        for r in rows:
            s = self._from_row(r)
            if fnmatch.fnmatch(task_type, s.task_type_pattern):
                out.append(s)
        return out

    async def record_outcome(self, strategy_id: str, *, success: bool) -> Strategy | None:
        """Record a live outcome. Candidate strategies accumulate evidence;
        activation additionally requires an offline eval_score."""
        st = await self.get(strategy_id)
        if st is None:
            return None
        evidence = st.evidence_count + 1
        rate = (st.success_rate * st.evidence_count + (1.0 if success else 0.0)) / evidence
        status = st.status
        if (
            status == StrategyStatus.CANDIDATE
            and evidence >= ACTIVATION_MIN_EVIDENCE
            and rate >= ACTIVATION_MIN_SUCCESS_RATE
            and st.eval_score is not None
            and st.eval_score >= 0.7
        ):
            status = StrategyStatus.ACTIVE
            _log.info("strategy.activated", event_type="memory", strategy_id=strategy_id, evidence=evidence, rate=rate)
        updated = st.model_copy(
            update={
                "evidence_count": evidence,
                "success_rate": rate,
                "status": status,
                "updated_ts": datetime.now(UTC),
            }
        )
        await self.save(updated)
        return updated

    @staticmethod
    def _from_row(row: object) -> Strategy:
        d = dict(row)  # type: ignore[call-overload]
        return Strategy(
            id=d["id"],
            task_type_pattern=d["task_type_pattern"],
            approach=d["approach"],
            model_preference=d["model_preference"],
            tool_preference=tuple(json.loads(d["tool_preference"])),
            status=d["status"],
            success_rate=d["success_rate"],
            evidence_count=d["evidence_count"],
            eval_score=d["eval_score"],
            created_ts=datetime.fromisoformat(d["created_ts"]),
            updated_ts=datetime.fromisoformat(d["updated_ts"]),
        )
