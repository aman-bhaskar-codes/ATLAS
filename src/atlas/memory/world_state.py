"""World state — lightweight entity tracking.

WHY: the agent re-derives facts about its environment every task (which repo
is open, which services are up, which files were recently touched). A small
typed-entity KV store caches those observations so context construction can
include "what we already know" instead of rediscovering it.

Deliberately minimal: entities are (type, id, attributes, updated_ts) rows.
No inference, no background syncing — writers are explicit (tools, platforms).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from atlas.infra.clock import Clock
from atlas.infra.db import Database


@dataclass(frozen=True)
class WorldEntity:
    entity_type: str  # repository | file | process | service | task | person | ...
    entity_id: str
    attributes: dict[str, Any]
    updated_ts: datetime


class WorldStateStore:
    def __init__(self, db: Database, clock: Clock) -> None:
        self._db = db
        self._clock = clock

    async def upsert(self, entity_type: str, entity_id: str, attributes: dict[str, Any]) -> None:
        await self._db.conn.execute(
            "INSERT INTO world_state (entity_type, entity_id, attributes, updated_ts) "
            "VALUES (?,?,?,?) "
            "ON CONFLICT(entity_type, entity_id) DO UPDATE SET "
            "attributes = excluded.attributes, updated_ts = excluded.updated_ts",
            (entity_type, entity_id, json.dumps(attributes), self._clock.now().isoformat()),
        )
        await self._db.conn.commit()

    async def patch(self, entity_type: str, entity_id: str, attributes: dict[str, Any]) -> None:
        """Merge attributes into an existing entity (create if absent)."""
        existing = await self.get(entity_type, entity_id)
        merged = {**(existing.attributes if existing else {}), **attributes}
        await self.upsert(entity_type, entity_id, merged)

    async def get(self, entity_type: str, entity_id: str) -> WorldEntity | None:
        cur = await self._db.conn.execute(
            "SELECT * FROM world_state WHERE entity_type = ? AND entity_id = ?",
            (entity_type, entity_id),
        )
        row = await cur.fetchone()
        return self._from_row(row) if row else None

    async def by_type(self, entity_type: str, limit: int = 50) -> list[WorldEntity]:
        cur = await self._db.conn.execute(
            "SELECT * FROM world_state WHERE entity_type = ? ORDER BY updated_ts DESC LIMIT ?",
            (entity_type, limit),
        )
        rows = await cur.fetchall()
        return [self._from_row(r) for r in rows]

    async def delete(self, entity_type: str, entity_id: str) -> None:
        await self._db.conn.execute(
            "DELETE FROM world_state WHERE entity_type = ? AND entity_id = ?",
            (entity_type, entity_id),
        )
        await self._db.conn.commit()

    async def to_prompt_fragment(self, limit: int = 10) -> str:
        """Most recently updated entities, rendered for prompt context."""
        cur = await self._db.conn.execute(
            "SELECT * FROM world_state ORDER BY updated_ts DESC LIMIT ?",
            (limit,),
        )
        rows = await cur.fetchall()
        if not rows:
            return ""
        lines = ["Known environment facts:"]
        for r in rows:
            e = self._from_row(r)
            attrs = "; ".join(f"{k}={v}" for k, v in list(e.attributes.items())[:4])
            lines.append(f"- {e.entity_type}/{e.entity_id}: {attrs}")
        return "\n".join(lines)

    @staticmethod
    def _from_row(row: object) -> WorldEntity:
        d = dict(row)  # type: ignore[call-overload]
        return WorldEntity(
            entity_type=d["entity_type"],
            entity_id=d["entity_id"],
            attributes=json.loads(d["attributes"]),
            updated_ts=datetime.fromisoformat(d["updated_ts"]),
        )
