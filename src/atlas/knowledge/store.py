"""FabricStore — durable persistence for the Knowledge Fabric (§3, §9, §98).

SQLite-backed. The vector store stays authoritative for similarity; SQL stays
authoritative for truth (documents, chunks, evidence, sessions, feedback).
BM25 rebuilds from SQL at startup — no separate lexical persistence format.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from atlas.infra.db import Database
from atlas.knowledge.domain import (
    AdapterState,
    Evidence,
    FabricChunk,
    FeedbackLabel,
    IngestionState,
    KnowledgeDocument,
    ResearchQuestionStatus,
    SecurityStatus,
    SourceType,
)


class FabricStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    # ── documents ────────────────────────────────────────────────────
    async def save_document(self, doc: KnowledgeDocument, chunks: list[FabricChunk]) -> None:
        now = doc.retrieved_at.isoformat()
        await self._db.conn.execute(
            """
            INSERT OR REPLACE INTO fabric_documents
            (document_id, source_id, source_type, title, uri, canonical_uri, content,
             content_type, language, author, published_at, retrieved_at, modified_at,
             content_hash, authority, trust_score, freshness, license, metadata_json,
             provenance_json, security_status, security_flags_json, pipeline_version,
             status, chunk_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                doc.document_id,
                doc.source_id,
                doc.source_type.value,
                doc.title,
                doc.uri,
                doc.canonical_uri,
                doc.content,
                doc.content_type,
                doc.language,
                doc.author,
                doc.published_at.isoformat() if doc.published_at else None,
                now,
                doc.modified_at.isoformat() if doc.modified_at else None,
                doc.content_hash,
                doc.authority,
                doc.trust_score,
                doc.freshness,
                doc.license,
                json.dumps(doc.metadata),
                json.dumps(doc.provenance),
                doc.security_status.value,
                json.dumps(list(doc.security_flags)),
                doc.pipeline_version,
                "READY",
                len(chunks),
            ),
        )
        await self._db.conn.execute("DELETE FROM fabric_chunks WHERE document_id = ?", (doc.document_id,))
        for c in chunks:
            await self._db.conn.execute(
                """
                INSERT OR REPLACE INTO fabric_chunks
                (chunk_id, document_id, content, heading, chunk_index, total_chunks,
                 char_start, char_end, token_estimate, embedding_id, kind)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    c.chunk_id,
                    c.document_id,
                    c.content,
                    c.heading,
                    c.chunk_index,
                    c.total_chunks,
                    c.char_start,
                    c.char_end,
                    c.token_estimate,
                    c.embedding_id,
                    c.kind,
                ),
            )
        await self._db.conn.commit()

    async def find_document_by_hash(self, content_hash: str) -> KnowledgeDocument | None:
        cur = await self._db.conn.execute(
            "SELECT * FROM fabric_documents WHERE content_hash = ? LIMIT 1", (content_hash,)
        )
        row = await cur.fetchone()
        return _row_to_document(row) if row else None

    async def get_document(self, document_id: str) -> KnowledgeDocument | None:
        cur = await self._db.conn.execute("SELECT * FROM fabric_documents WHERE document_id = ?", (document_id,))
        row = await cur.fetchone()
        return _row_to_document(row) if row else None

    async def list_documents(self, limit: int = 50) -> list[KnowledgeDocument]:
        cur = await self._db.conn.execute("SELECT * FROM fabric_documents ORDER BY retrieved_at DESC LIMIT ?", (limit,))
        rows = await cur.fetchall()
        return [d for r in rows if (d := _row_to_document(r)) is not None]

    async def delete_document(self, document_id: str) -> None:
        await self._db.conn.execute("DELETE FROM fabric_documents WHERE document_id = ?", (document_id,))
        await self._db.conn.commit()

    # ── deletion primitives (§12: granular forget) ───────────────────
    # These return WHAT they touched (ids, counts) so a coordinator can honestly
    # fan the same deletion out to the vector store and lexical index (§22). All
    # are deterministic SQL — no model, no vectors. fabric_chunks cascade from
    # fabric_documents via FK (PRAGMA foreign_keys=ON); fabric_evidence does NOT
    # (index only), so evidence is always deleted explicitly here.
    async def chunk_refs_for_document(self, document_id: str) -> list[tuple[str, str]]:
        """(chunk_id, embedding_id) pairs for a document — the vector purge list."""
        cur = await self._db.conn.execute(
            "SELECT chunk_id, embedding_id FROM fabric_chunks WHERE document_id = ?", (document_id,)
        )
        rows = await cur.fetchall()
        return [(r["chunk_id"], r["embedding_id"] or f"kc_{r['chunk_id']}") for r in rows]

    async def chunk_refs_all(self) -> list[tuple[str, str]]:
        cur = await self._db.conn.execute("SELECT chunk_id, embedding_id FROM fabric_chunks")
        rows = await cur.fetchall()
        return [(r["chunk_id"], r["embedding_id"] or f"kc_{r['chunk_id']}") for r in rows]

    async def all_document_ids(self) -> list[str]:
        cur = await self._db.conn.execute("SELECT document_id FROM fabric_documents")
        return [r["document_id"] for r in await cur.fetchall()]

    async def document_ids_for_source_type(self, source_type: SourceType) -> list[str]:
        cur = await self._db.conn.execute(
            "SELECT document_id FROM fabric_documents WHERE source_type = ?", (source_type.value,)
        )
        return [r["document_id"] for r in await cur.fetchall()]

    async def document_ids_for_uri(self, uri: str) -> list[str]:
        cur = await self._db.conn.execute(
            "SELECT document_id FROM fabric_documents WHERE uri = ? OR canonical_uri = ?", (uri, uri)
        )
        return [r["document_id"] for r in await cur.fetchall()]

    async def document_id_for_chunk(self, chunk_id: str) -> str | None:
        cur = await self._db.conn.execute("SELECT document_id FROM fabric_chunks WHERE chunk_id = ?", (chunk_id,))
        r = await cur.fetchone()
        return r["document_id"] if r else None

    async def delete_evidence(self, evidence_id: str) -> int:
        cur = await self._db.conn.execute("DELETE FROM fabric_evidence WHERE evidence_id = ?", (evidence_id,))
        await self._db.conn.commit()
        return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

    async def delete_evidence_for_chunk(self, chunk_id: str) -> int:
        cur = await self._db.conn.execute("DELETE FROM fabric_evidence WHERE chunk_id = ?", (chunk_id,))
        await self._db.conn.commit()
        return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

    async def delete_evidence_for_document(self, document_id: str) -> int:
        cur = await self._db.conn.execute("DELETE FROM fabric_evidence WHERE document_id = ?", (document_id,))
        await self._db.conn.commit()
        return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

    async def delete_chunk(self, chunk_id: str) -> int:
        cur = await self._db.conn.execute("DELETE FROM fabric_chunks WHERE chunk_id = ?", (chunk_id,))
        await self._db.conn.commit()
        return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

    async def evidence_id_exists(self, evidence_id: str) -> bool:
        cur = await self._db.conn.execute("SELECT 1 FROM fabric_evidence WHERE evidence_id = ? LIMIT 1", (evidence_id,))
        return await cur.fetchone() is not None

    async def count_evidence_for_chunk(self, chunk_id: str) -> int:
        cur = await self._db.conn.execute("SELECT COUNT(*) AS n FROM fabric_evidence WHERE chunk_id = ?", (chunk_id,))
        r = await cur.fetchone()
        return int(r["n"]) if r else 0

    async def count_evidence_for_document(self, document_id: str) -> int:
        cur = await self._db.conn.execute(
            "SELECT COUNT(*) AS n FROM fabric_evidence WHERE document_id = ?", (document_id,)
        )
        r = await cur.fetchone()
        return int(r["n"]) if r else 0

    async def count_all_evidence(self) -> int:
        cur = await self._db.conn.execute("SELECT COUNT(*) AS n FROM fabric_evidence")
        r = await cur.fetchone()
        return int(r["n"]) if r else 0

    # ── session deletion / unlink (§12) ──────────────────────────────
    async def delete_session(self, session_id: str) -> int:
        cur = await self._db.conn.execute("DELETE FROM research_sessions WHERE session_id = ?", (session_id,))
        await self._db.conn.commit()
        return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        cur = await self._db.conn.execute("SELECT * FROM research_sessions WHERE session_id = ?", (session_id,))
        r = await cur.fetchone()
        if r is None:
            return None
        return {
            "session_id": r["session_id"],
            "goal": r["goal"],
            "status": r["status"],
            "document_ids": json.loads(r["document_ids_json"]),
        }

    async def unlink_document_from_sessions(self, document_id: str) -> int:
        """Remove a deleted document's id from every session's document_ids_json so
        no session dangles a reference to a document that no longer exists (§22)."""
        cur = await self._db.conn.execute(
            "SELECT session_id, document_ids_json FROM research_sessions WHERE document_ids_json LIKE ?",
            (f'%"{document_id}"%',),
        )
        touched = 0
        for r in await cur.fetchall():
            ids = json.loads(r["document_ids_json"])
            if document_id in ids:
                remaining = [d for d in ids if d != document_id]
                await self._db.conn.execute(
                    "UPDATE research_sessions SET document_ids_json = ? WHERE session_id = ?",
                    (json.dumps(remaining), r["session_id"]),
                )
                touched += 1
        if touched:
            await self._db.conn.commit()
        return touched

    async def count_all_sessions(self) -> int:
        cur = await self._db.conn.execute("SELECT COUNT(*) AS n FROM research_sessions")
        r = await cur.fetchone()
        return int(r["n"]) if r else 0

    async def delete_all_sessions(self) -> int:
        cur = await self._db.conn.execute("DELETE FROM research_sessions")
        await self._db.conn.commit()
        return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

    async def set_document_status(self, document_id: str, state: IngestionState, error: str = "") -> None:
        await self._db.conn.execute(
            "UPDATE fabric_documents SET status = ? WHERE document_id = ?",
            (state.value, document_id),
        )
        await self._db.conn.commit()

    # ── chunks ───────────────────────────────────────────────────────
    async def all_chunks(self) -> list[tuple[FabricChunk, KnowledgeDocument]]:
        """Full corpus for BM25 rebuild and diagnostics. Bounded by ingest caps."""
        cur = await self._db.conn.execute(
            """
            SELECT c.chunk_id, c.document_id, c.content, c.heading, c.chunk_index,
                   c.total_chunks, c.char_start, c.char_end, c.token_estimate,
                   c.embedding_id, c.kind, d.*
            FROM fabric_chunks c JOIN fabric_documents d ON c.document_id = d.document_id
            WHERE d.status = 'READY' AND d.security_status != 'BLOCKED'
            """
        )
        rows = await cur.fetchall()
        out: list[tuple[FabricChunk, KnowledgeDocument]] = []
        for r in rows:
            doc = _row_to_document(r)
            if doc is None:
                continue
            chunk = FabricChunk(
                chunk_id=r["chunk_id"],
                document_id=r["document_id"],
                content=r["content"],
                heading=r["heading"],
                chunk_index=r["chunk_index"],
                total_chunks=r["total_chunks"],
                char_start=r["char_start"],
                char_end=r["char_end"],
                token_estimate=r["token_estimate"],
                embedding_id=r["embedding_id"],
                kind=r["kind"],
            )
            out.append((chunk, doc))
        return out

    async def get_chunk(self, chunk_id: str) -> tuple[FabricChunk, KnowledgeDocument] | None:
        cur = await self._db.conn.execute(
            """
            SELECT c.chunk_id, c.document_id, c.content, c.heading, c.chunk_index,
                   c.total_chunks, c.char_start, c.char_end, c.token_estimate,
                   c.embedding_id, c.kind, d.*
            FROM fabric_chunks c JOIN fabric_documents d ON c.document_id = d.document_id
            WHERE c.chunk_id = ?
            """,
            (chunk_id,),
        )
        r = await cur.fetchone()
        if r is None:
            return None
        doc = _row_to_document(r)
        if doc is None:
            return None
        chunk = FabricChunk(
            chunk_id=r["chunk_id"],
            document_id=r["document_id"],
            content=r["content"],
            heading=r["heading"],
            chunk_index=r["chunk_index"],
            total_chunks=r["total_chunks"],
            char_start=r["char_start"],
            char_end=r["char_end"],
            token_estimate=r["token_estimate"],
            embedding_id=r["embedding_id"],
            kind=r["kind"],
        )
        return chunk, doc

    # ── evidence ─────────────────────────────────────────────────────
    async def save_evidence(self, ev: Evidence) -> None:
        await self._db.conn.execute(
            """
            INSERT OR REPLACE INTO fabric_evidence
            (evidence_id, document_id, chunk_id, quote, location, authority,
             confidence, provenance_json, hash, created_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ev.evidence_id,
                ev.document_id,
                ev.chunk_id,
                ev.quote,
                ev.location,
                ev.authority,
                ev.confidence,
                json.dumps(ev.provenance),
                ev.hash,
                ev.retrieved_at.isoformat(),
            ),
        )
        await self._db.conn.commit()

    async def evidence_for_document(self, document_id: str) -> list[Evidence]:
        cur = await self._db.conn.execute(
            "SELECT * FROM fabric_evidence WHERE document_id = ? ORDER BY created_ts DESC",
            (document_id,),
        )
        rows = await cur.fetchall()
        return [
            Evidence(
                evidence_id=r["evidence_id"],
                document_id=r["document_id"],
                chunk_id=r["chunk_id"],
                source=SourceType.WEB_PAGE,
                quote=r["quote"],
                location=r["location"],
                retrieved_at=datetime.fromisoformat(r["created_ts"]),
                authority=r["authority"],
                confidence=r["confidence"],
                provenance=json.loads(r["provenance_json"]),
                hash=r["hash"],
            )
            for r in rows
        ]

    # ── research sessions (§9) ───────────────────────────────────────
    async def save_session(
        self,
        session_id: str,
        *,
        goal: str,
        status: ResearchQuestionStatus,
        questions: list[dict[str, Any]],
        visited_urls: list[str],
        document_ids: list[str],
        budget_used: dict[str, Any],
        started_ts: datetime,
        updated_ts: datetime,
    ) -> None:
        await self._db.conn.execute(
            """
            INSERT OR REPLACE INTO research_sessions
            (session_id, goal, status, questions_json, visited_urls_json,
             document_ids_json, budget_used_json, started_ts, updated_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                goal,
                status.value,
                json.dumps(questions),
                json.dumps(visited_urls),
                json.dumps(document_ids),
                json.dumps(budget_used),
                started_ts.isoformat(),
                updated_ts.isoformat(),
            ),
        )
        await self._db.conn.commit()

    async def recent_sessions(self, limit: int = 10) -> list[dict[str, Any]]:
        cur = await self._db.conn.execute("SELECT * FROM research_sessions ORDER BY updated_ts DESC LIMIT ?", (limit,))
        rows = await cur.fetchall()
        return [
            {
                "session_id": r["session_id"],
                "goal": r["goal"],
                "status": r["status"],
                "questions": json.loads(r["questions_json"]),
                "visited_urls": json.loads(r["visited_urls_json"]),
                "document_ids": json.loads(r["document_ids_json"]),
                "budget_used": json.loads(r["budget_used_json"]),
                "started_ts": r["started_ts"],
                "updated_ts": r["updated_ts"],
            }
            for r in rows
        ]

    async def find_session_by_goal(self, goal_fragment: str) -> dict[str, Any] | None:
        cur = await self._db.conn.execute(
            "SELECT * FROM research_sessions WHERE goal LIKE ? ORDER BY updated_ts DESC LIMIT 1",
            (f"%{goal_fragment}%",),
        )
        r = await cur.fetchone()
        if r is None:
            return None
        return {
            "session_id": r["session_id"],
            "goal": r["goal"],
            "status": r["status"],
            "questions": json.loads(r["questions_json"]),
            "visited_urls": json.loads(r["visited_urls_json"]),
            "document_ids": json.loads(r["document_ids_json"]),
            "budget_used": json.loads(r["budget_used_json"]),
            "started_ts": r["started_ts"],
            "updated_ts": r["updated_ts"],
        }

    # ── retrieval feedback (§125, §67) ───────────────────────────────
    async def record_feedback(
        self,
        query: str,
        chunk_id: str,
        document_id: str,
        label: FeedbackLabel,
        *,
        used_in_answer: bool = False,
        now: datetime | None = None,
    ) -> None:
        ts = (now or datetime.now(tz=UTC)).isoformat()
        await self._db.conn.execute(
            """
            INSERT INTO retrieval_feedback (query, chunk_id, document_id, label, used_in_answer, created_ts)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (query[:500], chunk_id, document_id, label.value, 1 if used_in_answer else 0, ts),
        )
        await self._db.conn.commit()

    async def feedback_pairs(self, limit: int = 5000) -> list[dict[str, Any]]:
        cur = await self._db.conn.execute(
            "SELECT query, chunk_id, document_id, label, used_in_answer FROM retrieval_feedback"
            " ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        rows = await cur.fetchall()
        return [
            {
                "query": r["query"],
                "chunk_id": r["chunk_id"],
                "document_id": r["document_id"],
                "label": r["label"],
                "used_in_answer": bool(r["used_in_answer"]),
            }
            for r in rows
        ]

    # ── adapter registry (§101) ──────────────────────────────────────
    async def upsert_adapter(
        self, kind: str, name: str, version: str, state: AdapterState, metrics: dict[str, float], now: datetime
    ) -> None:
        await self._db.conn.execute(
            """
            INSERT OR REPLACE INTO knowledge_adapters
            (name, kind, version, state, metrics_json, created_ts, updated_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (name, kind, version, state.value, json.dumps(metrics), now.isoformat(), now.isoformat()),
        )
        await self._db.conn.commit()

    async def adapters(self, kind: str) -> list[dict[str, Any]]:
        cur = await self._db.conn.execute(
            "SELECT name, version, state, metrics_json, updated_ts FROM knowledge_adapters WHERE kind = ?",
            (kind,),
        )
        rows = await cur.fetchall()
        return [
            {
                "name": r["name"],
                "version": r["version"],
                "state": r["state"],
                "metrics": json.loads(r["metrics_json"]),
                "updated_ts": r["updated_ts"],
            }
            for r in rows
        ]


def _row_to_document(r: Any) -> KnowledgeDocument | None:
    try:
        return KnowledgeDocument(
            document_id=r["document_id"],
            source_id=r["source_id"],
            source_type=SourceType(r["source_type"]),
            title=r["title"],
            uri=r["uri"],
            canonical_uri=r["canonical_uri"],
            content=r["content"],
            content_type=r["content_type"],
            language=r["language"],
            author=r["author"],
            published_at=datetime.fromisoformat(r["published_at"]) if r["published_at"] else None,
            retrieved_at=datetime.fromisoformat(r["retrieved_at"]),
            modified_at=datetime.fromisoformat(r["modified_at"]) if r["modified_at"] else None,
            content_hash=r["content_hash"],
            authority=r["authority"],
            trust_score=r["trust_score"],
            freshness=r["freshness"],
            license=r["license"],
            metadata=json.loads(r["metadata_json"] or "{}"),
            provenance=json.loads(r["provenance_json"] or "{}"),
            security_status=SecurityStatus(r["security_status"]),
            security_flags=tuple(json.loads(r["security_flags_json"] or "[]")),
            pipeline_version=r["pipeline_version"],
        )
    except Exception:
        return None
