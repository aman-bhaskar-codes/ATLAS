"""Auto-pruning — bounded memory via tiered compaction.

THE LADDER (never lose knowledge, only bulk):
  1. raw episodes, consolidated + older than HOT_DAYS + low salience
         -> summarized into a monthly memory_archive row, then deleted
  2. hard cap on total episodes: if exceeded, compact oldest first
  3. superseded semantic facts older than KEEP_SUPERSEDED_DAYS -> drop (history
     preserved in provenance + archive)
WHY salience-weighted: corrections and high-salience episodes survive longest.
WHY only-if-consolidated: we never delete an episode we haven't learned from.
"""

from __future__ import annotations

from datetime import timedelta

from atlas.infra.clock import Clock
from atlas.infra.db import Database
from atlas.infra.ids import IdGenerator
from atlas.infra.logging import get_logger
from atlas.infra.types import ModelRequest
from atlas.intelligence.gateway import ModelGateway

_log = get_logger("atlas.memory.pruning")

_HOT_DAYS = 30          # keep recent raw episodes verbatim
_MAX_EPISODES = 20_000  # hard cap on raw episodes
_SALIENCE_KEEP = 0.8    # never auto-prune episodes at/above this salience
_KEEP_SUPERSEDED_DAYS = 90


class Pruner:
    def __init__(
        self, *, db: Database, gateway: ModelGateway, ids: IdGenerator, clock: Clock,
    ) -> None:
        self._db = db
        self._gw = gateway
        self._ids = ids
        self._clock = clock

    async def run(self) -> dict[str, int]:
        archived = await self._compact_cold_episodes()
        capped = await self._enforce_episode_cap()
        dropped = await self._drop_old_superseded_facts()
        _log.info("pruning.done", event_type="memory",
                  archived=archived, capped=capped, dropped_facts=dropped)
        return {"archived": archived, "capped": capped, "dropped_facts": dropped}

    async def _compact_cold_episodes(self) -> int:
        cutoff = (self._clock.now() - timedelta(days=_HOT_DAYS)).isoformat()
        cur = await self._db.conn.execute(
            "SELECT id, ts, content FROM episodes "
            "WHERE consolidated=1 AND ts < ? AND salience < ? ORDER BY ts LIMIT 1000",
            (cutoff, _SALIENCE_KEEP),
        )
        rows = list(await cur.fetchall())
        if not rows:
            return 0
        period = str(rows[0]["ts"])[:7]  # YYYY-MM
        blob = "\n".join(str(r["content"])[:300] for r in rows)
        resp = await self._gw.complete(ModelRequest(
            correlation_id=self._ids.correlation_id(),
            system="Summarize these old episodes into a short factual narrative.",
            prompt=blob, max_tokens=400,
        ))
        await self._db.conn.execute(
            "INSERT INTO memory_archive(period, summary, episode_count, created_ts) "
            "VALUES (?,?,?,?)",
            (period, resp.text, len(rows), self._clock.now().isoformat()),
        )
        ids = [r["id"] for r in rows]
        qs = ",".join("?" for _ in ids)
        await self._db.conn.execute(f"DELETE FROM episodes WHERE id IN ({qs})", ids)
        await self._db.conn.commit()
        return len(ids)

    async def _enforce_episode_cap(self) -> int:
        cur = await self._db.conn.execute("SELECT COUNT(*) AS c FROM episodes")
        row = await cur.fetchone()
        total = int(row["c"]) if row else 0
        if total <= _MAX_EPISODES:
            return 0
        excess = total - _MAX_EPISODES
        # delete oldest, lowest-salience, already-consolidated first
        await self._db.conn.execute(
            "DELETE FROM episodes WHERE id IN ("
            "  SELECT id FROM episodes WHERE consolidated=1 AND salience < ? "
            "  ORDER BY ts LIMIT ?)",
            (_SALIENCE_KEEP, excess),
        )
        await self._db.conn.commit()
        return excess

    async def _drop_old_superseded_facts(self) -> int:
        cutoff = (self._clock.now() - timedelta(days=_KEEP_SUPERSEDED_DAYS)).isoformat()
        cur = await self._db.conn.execute(
            "DELETE FROM semantic_facts WHERE superseded_by IS NOT NULL AND updated_ts < ?",
            (cutoff,),
        )
        await self._db.conn.commit()
        return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
