"""User-model — the always-on context block.

WHY separate from semantic retrieval: some things must be in EVERY prompt (your
name, current focus, standing preferences), not fetched only when a query
happens to match. Bounded to a hard token cap so it can't crowd out the task.
Updates are proposed by consolidation and require your sign-off (Tier-2) — this
is your identity, the highest-value memory in the system.
"""

from __future__ import annotations

from atlas.infra.clock import Clock
from atlas.infra.db import Database

_SECTIONS = ("identity", "routine", "active_projects", "preferences", "goals")
_MAX_CHARS = 3200  # ~800 tokens hard cap across all sections


class UserModel:
    def __init__(self, db: Database, clock: Clock) -> None:
        self._db = db
        self._clock = clock

    async def set_section(self, section: str, content: str) -> None:
        if section not in _SECTIONS:
            raise ValueError(f"unknown section {section!r}; allowed: {_SECTIONS}")
        now = self._clock.now().isoformat()
        await self._db.conn.execute(
            "INSERT INTO user_model(section, content, version, updated_ts) VALUES (?,?,1,?) "
            "ON CONFLICT(section) DO UPDATE SET content=excluded.content, "
            "version=user_model.version+1, updated_ts=excluded.updated_ts",
            (section, content, now),
        )
        await self._db.conn.commit()

    async def render(self) -> str:
        cur = await self._db.conn.execute("SELECT section, content FROM user_model")
        rows = {r["section"]: r["content"] for r in await cur.fetchall()}
        parts: list[str] = []
        for s in _SECTIONS:
            if s in rows and rows[s].strip():
                parts.append(f"{s}: {rows[s].strip()}")
        text = "\n".join(parts)
        return text[:_MAX_CHARS]  # hard cap — never let it crowd the task
