"""Small durable idempotency boundary for Trust Center mutations."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from atlas.infra.db import Database


class IdempotencyConflict(Exception):
    pass


class IdempotencyStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    @staticmethod
    def fingerprint(payload: dict[str, Any]) -> str:
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(raw.encode()).hexdigest()

    async def get(self, key: str) -> tuple[str, str] | None:
        cur = await self._db.conn.execute(
            "SELECT fingerprint, response_json FROM idempotency_keys WHERE key=?",
            (key,),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return str(row["fingerprint"]), str(row["response_json"])

    async def put(self, key: str, fingerprint: str, response_json: str) -> None:
        await self._db.conn.execute(
            "INSERT INTO idempotency_keys(key, fingerprint, response_json, created_ts) "
            "VALUES (?, ?, ?, datetime('now'))",
            (key, fingerprint, response_json),
        )
        await self._db.conn.commit()
