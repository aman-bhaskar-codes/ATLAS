"""Curated memory — the always-loaded tier, written only by consolidation.

WHY a separate tier at all: recall quality is a *write-time* problem. A small,
deduped, human-inspectable document that is loaded on every session start beats a
large index queried on every turn — it costs one indexed row read instead of an
embedding call plus a vector round-trip, and you can read it to see exactly what
the agent believes.

WHY compare-and-swap instead of a lock: consolidation runs in the background and
must never block a live turn, so it cannot hold a write lock across a bounded
model call. Instead it captures ``content_hash`` before the call and swaps on it
afterwards. If anything else wrote in that window the UPDATE matches zero rows
and the sweep aborts rather than clobbering the newer content — the same
discipline as a hash-checked atomic file rename.

WHY ``pre_image``: one bad merge should be recoverable without reaching for a
backup, so every swap keeps the previous content inline for a one-step revert.
"""

from __future__ import annotations

import hashlib

from atlas.infra.clock import Clock
from atlas.infra.db import Database
from atlas.infra.logging import get_logger
from atlas.memory.types import CuratedDoc

_log = get_logger("atlas.memory.curated")

#: The curated surfaces. MEMORY is durable knowledge, USER is who you are.
MEMORY_KEY = "MEMORY"
USER_KEY = "USER"


def content_hash(content: str) -> str:
    """Stable hash used as the compare-and-swap token.

    sha256 over UTF-8 — not a security boundary, just a change detector, but a
    cryptographic digest costs nothing here and removes any chance of a
    collision silently permitting a lost update.
    """
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class CuratedMemory:
    """Read/write the curated tier with optimistic concurrency."""

    def __init__(self, db: Database, clock: Clock) -> None:
        self._db = db
        self._clock = clock

    async def get(self, file_key: str) -> CuratedDoc | None:
        cur = await self._db.conn.execute(
            "SELECT file_key, content, content_hash, pre_image, pre_image_hash, version, updated_ts "
            "FROM curated_memory WHERE file_key = ?",
            (file_key,),
        )
        row = await cur.fetchone()
        return None if row is None else self._row(row)

    async def bootstrap(self, keys: tuple[str, ...] = (MEMORY_KEY, USER_KEY)) -> str:
        """Render the curated tier for injection at session start.

        One indexed read per surface, no embeddings, no model call. Missing
        surfaces are simply absent — a fresh install renders an empty string
        rather than inventing a placeholder the model might treat as content.
        """
        parts: list[str] = []
        for key in keys:
            doc = await self.get(key)
            if doc is not None and doc.content.strip():
                parts.append(f"## {key}\n{doc.content.strip()}")
        return "\n\n".join(parts)

    async def create_if_absent(self, file_key: str, content: str = "") -> CuratedDoc:
        """Ensure a surface exists so consolidation always has a hash to swap on."""
        existing = await self.get(file_key)
        if existing is not None:
            return existing
        now = self._clock.now().isoformat()
        await self._db.conn.execute(
            "INSERT OR IGNORE INTO curated_memory"
            "(file_key, content, content_hash, pre_image, pre_image_hash, version, updated_ts) "
            "VALUES (?,?,?,NULL,NULL,1,?)",
            (file_key, content, content_hash(content), now),
        )
        await self._db.conn.commit()
        created = await self.get(file_key)
        if created is None:  # pragma: no cover — INSERT OR IGNORE then missing is impossible
            raise RuntimeError(f"curated_memory row for {file_key!r} vanished after insert")
        return created

    async def swap(self, file_key: str, *, new_content: str, expected_hash: str) -> bool:
        """Compare-and-swap ``content``. Returns False if someone else wrote first.

        A False return is a normal outcome, not an error: it means the caller's
        read is stale and the safe move is to abandon this sweep and recompute
        next run. Callers must not retry in a loop — that reintroduces the lost
        update this guard exists to prevent.
        """
        now = self._clock.now().isoformat()
        cur = await self._db.conn.execute(
            "UPDATE curated_memory SET "
            "  pre_image = content, pre_image_hash = content_hash, "
            "  content = ?, content_hash = ?, version = version + 1, updated_ts = ? "
            "WHERE file_key = ? AND content_hash = ?",
            (new_content, content_hash(new_content), now, file_key, expected_hash),
        )
        await self._db.conn.commit()
        won = cur.rowcount == 1
        if not won:
            _log.warning(
                "curated.swap_conflict",
                event_type="memory",
                file_key=file_key,
                detail="content changed since the sweep started; aborting this write",
            )
        return won

    async def append(self, file_key: str, line: str) -> bool:
        """Fallback path: append one line without a merge.

        WHY this exists: when the merge output fails validation, losing the
        candidate entirely is worse than keeping it unmerged. Appending is
        always safe — it never removes an existing entry — so it is the
        designated degradation for a failed sweep.
        """
        doc = await self.create_if_absent(file_key)
        addition = line.strip()
        if not addition:
            return False
        merged = f"{doc.content.rstrip()}\n{addition}\n" if doc.content.strip() else f"{addition}\n"
        return await self.swap(file_key, new_content=merged, expected_hash=doc.content_hash)

    async def revert(self, file_key: str) -> bool:
        """Restore ``pre_image`` — the one-step undo for a bad sweep."""
        doc = await self.get(file_key)
        if doc is None or doc.pre_image is None:
            return False
        return await self.swap(file_key, new_content=doc.pre_image, expected_hash=doc.content_hash)

    @staticmethod
    def _row(row: object) -> CuratedDoc:
        from datetime import datetime

        d = dict(row)  # type: ignore[call-overload]
        return CuratedDoc(
            file_key=d["file_key"],
            content=d["content"],
            content_hash=d["content_hash"],
            pre_image=d["pre_image"],
            pre_image_hash=d["pre_image_hash"],
            version=d["version"],
            updated_ts=datetime.fromisoformat(d["updated_ts"]),
        )
