"""Semantic memory — distilled, versioned knowledge.

WHY supersede instead of overwrite: 'I used to prefer X, now Y' must be
reconstructable. Updating a fact creates a new version and points the old one's
superseded_by at it. WHY embeddings via the gateway: bge-m3 locally, $0, and
the one embedding path is audited/metered like any model call.
"""

from __future__ import annotations

import json

from atlas.infra.clock import Clock
from atlas.infra.db import Database
from atlas.infra.ids import IdGenerator
from atlas.memory.embedder import Embedder
from atlas.memory.types import FactKind, SemanticFact
from atlas.memory.vectorstore import VectorStore


class SemanticMemory:
    def __init__(
        self, db: Database, vectors: VectorStore, embedder: Embedder,
        ids: IdGenerator, clock: Clock,
    ) -> None:
        self._db = db
        self._vectors = vectors
        self._embedder = embedder
        self._ids = ids
        self._clock = clock

    async def add_fact(
        self, text: str, kind: FactKind, *, confidence: float,
        salience: float, sources: tuple[int, ...] | list[int],
    ) -> str:
        fid = self._ids.execution_id()
        now = self._clock.now()
        emb = await self._embedder.embed(text)
        await self._vectors.upsert(fid, text, emb)
        await self._db.conn.execute(
            "INSERT INTO semantic_facts(id, version, text, kind, confidence, salience, "
            "source_episode_ids, superseded_by, created_ts, updated_ts, embedding_ref) "
            "VALUES (?,1,?,?,?,?,?,NULL,?,?,?)",
            (fid, text, kind.value, confidence, salience,
             json.dumps(list(sources)), now.isoformat(), now.isoformat(), fid),
        )
        await self._db.conn.commit()
        return fid

    async def supersede(self, old_id: str, new_text: str, *, confidence: float) -> str:
        """Version a changed fact. Old stays for history, marked superseded."""
        cur = await self._db.conn.execute(
            "SELECT kind, salience, version FROM semantic_facts WHERE id=?", (old_id,)
        )
        row = await cur.fetchone()
        if row is None:
            raise KeyError(old_id)
        new_id = await self.add_fact(
            new_text, FactKind(row["kind"]), confidence=confidence,
            salience=float(row["salience"]), sources=(),
        )
        await self._db.conn.execute(
            "UPDATE semantic_facts SET superseded_by=?, updated_ts=? WHERE id=?",
            (new_id, self._clock.now().isoformat(), old_id),
        )
        await self._db.conn.commit()
        return new_id

    async def semantic_search(self, query: str, k: int) -> list[SemanticFact]:
        emb = await self._embedder.embed(query)
        hits = await self._vectors.query(emb, k)
        if not hits:
            return []
        refs = [h.ref for h in hits]
        qs = ",".join("?" for _ in refs)
        cur = await self._db.conn.execute(
            f"SELECT * FROM semantic_facts WHERE id IN ({qs}) AND superseded_by IS NULL", refs
        )
        by_id = {r["id"]: self._row(r) for r in await cur.fetchall()}
        # preserve vector rank order
        return [by_id[ref] for ref in refs if ref in by_id]

    @staticmethod
    def _row(r: object) -> SemanticFact:
        from datetime import datetime
        d = dict(r)  # type: ignore[call-overload]
        return SemanticFact(
            id=d["id"], version=d["version"], text=d["text"], kind=FactKind(d["kind"]),
            confidence=d["confidence"], salience=d["salience"],
            source_episode_ids=tuple(json.loads(d["source_episode_ids"] or "[]")),
            superseded_by=d["superseded_by"],
            created_ts=datetime.fromisoformat(d["created_ts"]),
            updated_ts=datetime.fromisoformat(d["updated_ts"]),
        )
