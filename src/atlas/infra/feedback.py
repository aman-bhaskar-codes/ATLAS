"""Feedback store — records user feedback on task outcomes.

WHY: the Vamos feedback loop requires storing thumbs up/down ratings plus
user edits (original vs edited output) so the evaluation/learner can
improve prompts and model selection over time.
"""

from __future__ import annotations

import json
from datetime import datetime

from atlas.infra.clock import Clock
from atlas.infra.db import Database
from atlas.infra.ids import IdGenerator


class FeedbackStore:
    def __init__(self, db: Database, ids: IdGenerator, clock: Clock) -> None:
        self._db = db
        self._ids = ids
        self._clock = clock

    async def record(
        self, *, task_id: str, rating: int,
        comment: str | None = None,
        original_output: str | None = None,
        edited_output: str | None = None,
    ) -> str:
        """Record user feedback. rating must be -1 (thumbs down) or 1 (thumbs up)."""
        if rating not in (-1, 1):
            raise ValueError(f"rating must be -1 or 1, got {rating}")
        fid = self._ids.new()
        await self._db.conn.execute(
            "INSERT INTO feedback(id, task_id, rating, comment, original_output, "
            "edited_output, created_ts) VALUES (?,?,?,?,?,?,?)",
            (fid, task_id, rating, comment, original_output, edited_output,
             self._clock.now().isoformat()),
        )
        await self._db.conn.commit()
        return fid

    async def for_task(self, task_id: str) -> list[dict[str, object]]:
        cur = await self._db.conn.execute(
            "SELECT * FROM feedback WHERE task_id=? ORDER BY created_ts", (task_id,)
        )
        return [dict(r) for r in await cur.fetchall()]

    async def recent(self, limit: int = 50) -> list[dict[str, object]]:
        cur = await self._db.conn.execute(
            "SELECT * FROM feedback ORDER BY created_ts DESC LIMIT ?", (limit,)
        )
        rows = list(await cur.fetchall())
        return [dict(r) for r in reversed(rows)]

    async def stats(self) -> dict[str, int]:
        """Return aggregate feedback statistics."""
        cur = await self._db.conn.execute(
            "SELECT rating, COUNT(*) as cnt FROM feedback GROUP BY rating"
        )
        rows = await cur.fetchall()
        result = {"positive": 0, "negative": 0, "total": 0}
        for row in rows:
            if int(row["rating"]) == 1:
                result["positive"] = int(row["cnt"])
            else:
                result["negative"] = int(row["cnt"])
            result["total"] += int(row["cnt"])
        return result
