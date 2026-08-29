"""Research memory deletion — granular, coordinated, honest (§11, §12, §22).

WHY this exists: a single document write fans out across FOUR stores — the SQL
truth (`fabric_documents`/`fabric_chunks`/`fabric_evidence`), the dense vector
collection (`kc_<chunk_id>` in Chroma), the in-memory BM25 lexical index + chunk
cache, and the `research_sessions` links that point at it. `store.delete_document`
alone leaves three of those behind (evidence rows do NOT cascade, vectors and the
lexical index are separate stores, sessions keep a dangling id). A partial delete
is a correctness AND a privacy bug: the user asked to forget something and it
half-survives. `ResearchMemory` is the ONE coordinator that fans a forget out to
every store and reports exactly what it touched.

§11 boundary: this operates ONLY on the research corpus (fabric_* + the knowledge
Chroma collection + research_sessions). It NEVER touches personal trusted memory
(`semantic_facts`/`episodes`) — external research is DATA and was never promoted
into trusted memory, so deleting research can never delete the user's own facts.

§22 honesty: every scope returns a `DeletionReport` with real per-store counts. A
`dry_run` computes those counts WITHOUT mutating, so a caller can preview a forget
(and an approval-gated UI can show "this will remove N documents, M vectors")
before committing. Vector purges are best-effort and counted truthfully: if the
vector store is down, SQL still becomes the truth and the report says how many
vectors were actually removed.

Determinism: no model call. Same corpus + same scope in → same report out.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Protocol

from atlas.infra.logging import get_logger
from atlas.knowledge.domain import SourceType
from atlas.knowledge.store import FabricStore

_log = get_logger("atlas.knowledge.deletion")


class DeletionScope(enum.Enum):
    """The seven granularities at which research memory can be forgotten (§12)."""

    EVIDENCE = "evidence"  # one extracted quote (annotation) — chunk/doc untouched
    CHUNK = "chunk"  # one chunk: SQL row + its vector + lexical + evidence on it
    DOCUMENT = "document"  # one source: doc (chunks cascade) + vectors + evidence + lexical
    SESSION = "session"  # one research session record (optionally its documents)
    SOURCE_TYPE = "source_type"  # every document of a SourceType (e.g. all web_page)
    URI = "uri"  # every document from one source URI / canonical URI
    ALL = "all"  # the entire research corpus (documents, chunks, evidence, sessions)


class _LexicalIndex(Protocol):
    def drop_document(self, document_id: str) -> None: ...
    def drop_chunk(self, chunk_id: str) -> None: ...
    def drop_all(self) -> None: ...


class _VectorStore(Protocol):
    async def delete_knowledge_chunk(self, embedding_id: str) -> None: ...


@dataclass(frozen=True)
class DeletionReport:
    """Exactly what a forget touched, per store (§22). `dry_run` reports would-touch
    counts without mutating anything."""

    scope: DeletionScope
    target: str
    dry_run: bool = False
    documents: int = 0
    chunks: int = 0
    evidence: int = 0
    sessions: int = 0
    vectors: int = 0
    lexical: int = 0
    vectors_failed: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        verb = "Would remove" if self.dry_run else "Removed"
        parts = [
            f"{self.documents} documents",
            f"{self.chunks} chunks",
            f"{self.evidence} evidence",
            f"{self.sessions} sessions",
            f"{self.vectors} vectors",
        ]
        tail = ""
        if self.vectors_failed:
            tail = f" ({self.vectors_failed} vectors could not be purged and remain orphaned)"
        return f"{verb} {', '.join(parts)} for {self.scope.value}={self.target!r}{tail}."


class ResearchMemory:
    """Coordinates deletion across SQL, vector store, and the lexical index (§12).

    Holds no state of its own — every count is read live from the stores so the
    report can never drift from reality.
    """

    def __init__(self, store: FabricStore, retriever: _LexicalIndex, vector: _VectorStore | None) -> None:
        self._store = store
        self._retriever = retriever
        self._vector = vector

    async def forget(
        self,
        scope: DeletionScope,
        target: str = "",
        *,
        cascade_documents: bool = False,
        dry_run: bool = False,
    ) -> DeletionReport:
        """Forget research memory at one of the seven granularities.

        `target` is the id/value the scope selects by (evidence_id, chunk_id,
        document_id, session_id, SourceType value, or URI); ignored for ALL.
        `cascade_documents` only affects SESSION scope: when true the session's
        linked documents are deleted too, otherwise the session row is removed and
        its documents are left in place (shared documents are not silently nuked).
        """
        if scope is not DeletionScope.ALL and not target:
            raise ValueError(f"scope {scope.value} requires a non-empty target")
        if scope is DeletionScope.EVIDENCE:
            return await self._forget_evidence(target, dry_run)
        if scope is DeletionScope.CHUNK:
            return await self._forget_chunk(target, dry_run)
        if scope is DeletionScope.DOCUMENT:
            return await self._forget_document(target, dry_run)
        if scope is DeletionScope.SESSION:
            return await self._forget_session(target, cascade_documents, dry_run)
        if scope is DeletionScope.SOURCE_TYPE:
            return await self._forget_source_type(target, dry_run)
        if scope is DeletionScope.URI:
            return await self._forget_uri(target, dry_run)
        return await self._forget_all(dry_run)

    # ── vector purge (best-effort, honestly counted) ─────────────────
    async def _purge_vectors(self, refs: list[tuple[str, str]]) -> tuple[int, int]:
        """Delete each chunk's vector by its embedding_id. Returns (purged, failed).
        Idempotent: a missing vector counts as purged. Never raises."""
        if self._vector is None:
            return (0, 0)
        purged = failed = 0
        for _chunk_id, embedding_id in refs:
            try:
                await self._vector.delete_knowledge_chunk(embedding_id)
                purged += 1
            except Exception as exc:  # a dead vector store must not abort the SQL delete
                failed += 1
                _log.warning("research_memory.vector_purge_failed", event_type="knowledge", error=repr(exc))
        return (purged, failed)

    # ── per-scope handlers ───────────────────────────────────────────
    async def _forget_evidence(self, evidence_id: str, dry_run: bool) -> DeletionReport:
        exists = await self._store.evidence_id_exists(evidence_id)
        n = 1 if exists else 0
        if not dry_run and exists:
            n = await self._store.delete_evidence(evidence_id)
        return DeletionReport(scope=DeletionScope.EVIDENCE, target=evidence_id, dry_run=dry_run, evidence=n)

    async def _forget_chunk(self, chunk_id: str, dry_run: bool) -> DeletionReport:
        document_id = await self._store.document_id_for_chunk(chunk_id)
        refs: list[tuple[str, str]] = []
        if document_id is not None:
            all_refs = await self._store.chunk_refs_for_document(document_id)
            refs = [(cid, emb) for cid, emb in all_refs if cid == chunk_id]
        ev_count = await self._store.count_evidence_for_chunk(chunk_id)
        if dry_run:
            return DeletionReport(
                scope=DeletionScope.CHUNK,
                target=chunk_id,
                dry_run=True,
                chunks=1 if refs else 0,
                evidence=ev_count,
                vectors=len(refs),
            )
        purged, failed = await self._purge_vectors(refs)
        ev_deleted = await self._store.delete_evidence_for_chunk(chunk_id)
        chunk_deleted = await self._store.delete_chunk(chunk_id)
        self._retriever.drop_chunk(chunk_id)
        return DeletionReport(
            scope=DeletionScope.CHUNK,
            target=chunk_id,
            chunks=chunk_deleted,
            evidence=ev_deleted,
            vectors=purged,
            vectors_failed=failed,
            lexical=chunk_deleted,
        )

    async def _forget_document(self, document_id: str, dry_run: bool) -> DeletionReport:
        refs = await self._store.chunk_refs_for_document(document_id)
        ev_count = await self._store.count_evidence_for_document(document_id)
        exists = await self._store.get_document(document_id) is not None
        if dry_run:
            return DeletionReport(
                scope=DeletionScope.DOCUMENT,
                target=document_id,
                dry_run=True,
                documents=1 if exists else 0,
                chunks=len(refs),
                evidence=ev_count,
                vectors=len(refs),
            )
        purged, failed = await self._purge_vectors(refs)
        ev_deleted = await self._store.delete_evidence_for_document(document_id)
        await self._store.delete_document(document_id)  # chunks cascade via FK
        unlinked = await self._store.unlink_document_from_sessions(document_id)
        self._retriever.drop_document(document_id)
        return DeletionReport(
            scope=DeletionScope.DOCUMENT,
            target=document_id,
            documents=1 if exists else 0,
            chunks=len(refs),
            evidence=ev_deleted,
            vectors=purged,
            vectors_failed=failed,
            lexical=len(refs),
            notes=[f"unlinked from {unlinked} session(s)"] if unlinked else [],
        )

    async def _forget_session(self, session_id: str, cascade_documents: bool, dry_run: bool) -> DeletionReport:
        session = await self._store.get_session(session_id)
        if session is None:
            return DeletionReport(scope=DeletionScope.SESSION, target=session_id, dry_run=dry_run)
        doc_ids: list[str] = list(session.get("document_ids", [])) if cascade_documents else []
        agg = _Acc()
        if cascade_documents:
            for did in doc_ids:
                agg.add(await self._forget_document(did, dry_run))
        if not dry_run:
            agg.sessions += await self._store.delete_session(session_id)
        else:
            agg.sessions += 1
        return agg.finish(
            DeletionScope.SESSION,
            session_id,
            dry_run,
            notes=[f"cascaded {len(doc_ids)} document(s)"] if cascade_documents and doc_ids else [],
        )

    async def _forget_documents(
        self, doc_ids: list[str], scope: DeletionScope, target: str, dry_run: bool
    ) -> DeletionReport:
        agg = _Acc()
        for did in doc_ids:
            agg.add(await self._forget_document(did, dry_run))
        return agg.finish(scope, target, dry_run)

    async def _forget_source_type(self, source_type_value: str, dry_run: bool) -> DeletionReport:
        try:
            st = SourceType(source_type_value)
        except ValueError as exc:
            raise ValueError(f"unknown source_type: {source_type_value!r}") from exc
        doc_ids = await self._store.document_ids_for_source_type(st)
        return await self._forget_documents(doc_ids, DeletionScope.SOURCE_TYPE, source_type_value, dry_run)

    async def _forget_uri(self, uri: str, dry_run: bool) -> DeletionReport:
        doc_ids = await self._store.document_ids_for_uri(uri)
        return await self._forget_documents(doc_ids, DeletionScope.URI, uri, dry_run)

    async def _forget_all(self, dry_run: bool) -> DeletionReport:
        doc_ids = await self._store.all_document_ids()
        refs = await self._store.chunk_refs_all()
        ev_count = await self._store.count_all_evidence()
        sess_count = await self._store.count_all_sessions()
        if dry_run:
            return DeletionReport(
                scope=DeletionScope.ALL,
                target="*",
                dry_run=True,
                documents=len(doc_ids),
                chunks=len(refs),
                evidence=ev_count,
                sessions=sess_count,
                vectors=len(refs),
            )
        purged, failed = await self._purge_vectors(refs)
        ev_deleted = 0
        for did in doc_ids:
            ev_deleted += await self._store.delete_evidence_for_document(did)
            await self._store.delete_document(did)  # chunks cascade
        sess_deleted = await self._store.delete_all_sessions()
        self._retriever.drop_all()
        return DeletionReport(
            scope=DeletionScope.ALL,
            target="*",
            documents=len(doc_ids),
            chunks=len(refs),
            evidence=ev_deleted,
            sessions=sess_deleted,
            vectors=purged,
            vectors_failed=failed,
            lexical=len(refs),
        )


@dataclass
class _Acc:
    """Sums child DeletionReports into one honest aggregate."""

    documents: int = 0
    chunks: int = 0
    evidence: int = 0
    sessions: int = 0
    vectors: int = 0
    lexical: int = 0
    vectors_failed: int = 0

    def add(self, r: DeletionReport) -> None:
        self.documents += r.documents
        self.chunks += r.chunks
        self.evidence += r.evidence
        self.sessions += r.sessions
        self.vectors += r.vectors
        self.lexical += r.lexical
        self.vectors_failed += r.vectors_failed

    def finish(
        self, scope: DeletionScope, target: str, dry_run: bool, *, notes: list[str] | None = None
    ) -> DeletionReport:
        return DeletionReport(
            scope=scope,
            target=target,
            dry_run=dry_run,
            documents=self.documents,
            chunks=self.chunks,
            evidence=self.evidence,
            sessions=self.sessions,
            vectors=self.vectors,
            lexical=self.lexical,
            vectors_failed=self.vectors_failed,
            notes=notes or [],
        )
