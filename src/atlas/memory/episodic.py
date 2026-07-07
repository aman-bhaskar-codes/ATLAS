"""Episodic memory — the raw log.

WHY salience on write: corrections (user overrides the agent) are the highest-
signal events we own, so they're written with high salience and are the last to
be pruned. WHY `consolidated` flag: we NEVER prune an episode that hasn't been
distilled into semantic memory yet — no data loss before learning from it.
"""

from __future__ import annotations

from atlas.infra.clock import Clock
from atlas.infra.db import Database
from atlas.memory.types import Episode, EpisodeKind


class EpisodicMemory:
    def __init__(self, db: Database, clock: Clock) -> None:
        self._db = db
        self._clock = clock

    async def record(self, ep: Episode) -> int:
        cur = await self._db.conn.execute(
            "INSERT INTO episodes(correlation_id, task_id, step, ts, kind, role, "
            "content, tool, outcome, salience, consolidated, tokens) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,0,?)",
            (ep.correlation_id, ep.task_id, ep.step, ep.ts.isoformat(), ep.kind.value,
             ep.role, ep.content, ep.tool, ep.outcome, ep.salience, ep.tokens),
        )
        await self._db.conn.commit()
        return int(cur.lastrowid) if cur.lastrowid is not None else -1

    async def record_correction(self, correlation_id: str, content: str) -> int:
        """Corrections get max salience — this is how the agent learns you."""
        return await self.record(Episode(
            correlation_id=correlation_id, ts=self._clock.now(),
            kind=EpisodeKind.CORRECTION, role="user", content=content, salience=1.0,
        ))

    async def recent(self, limit: int = 50) -> list[Episode]:
        cur = await self._db.conn.execute(
            "SELECT * FROM episodes ORDER BY id DESC LIMIT ?", (limit,)
        )
        rows = list(await cur.fetchall())
        return [self._row(r) for r in reversed(rows)]

    async def unconsolidated(self, limit: int = 500) -> list[Episode]:
        cur = await self._db.conn.execute(
            "SELECT * FROM episodes WHERE consolidated=0 ORDER BY id LIMIT ?", (limit,)
        )
        return [self._row(r) for r in await cur.fetchall()]

    async def keyword_search(self, terms: list[str], limit: int = 20) -> list[Episode]:
        """Sparse retrieval over episodic content (exact names/paths dense misses)."""
        if not terms:
            return []
        like = " OR ".join(["content LIKE ?" for _ in terms])
        params = [f"%{t}%" for t in terms] + [limit]
        cur = await self._db.conn.execute(
            f"SELECT * FROM episodes WHERE {like} ORDER BY salience DESC, id DESC LIMIT ?",
            params,
        )
        return [self._row(r) for r in await cur.fetchall()]

    async def mark_consolidated(self, ids: list[int]) -> None:
        if not ids:
            return
        qs = ",".join("?" for _ in ids)
        await self._db.conn.execute(
            f"UPDATE episodes SET consolidated=1 WHERE id IN ({qs})", ids
        )
        await self._db.conn.commit()

    @staticmethod
    def _row(r: object) -> Episode:
        from datetime import datetime
        d = dict(r)  # type: ignore[call-overload]
        return Episode(
            id=d["id"], correlation_id=d["correlation_id"], task_id=d["task_id"],
            step=d["step"], ts=datetime.fromisoformat(d["ts"]),
            kind=EpisodeKind(d["kind"]), role=d["role"], content=d["content"],
            tool=d["tool"], outcome=d["outcome"], salience=d["salience"], tokens=d["tokens"],
        )
