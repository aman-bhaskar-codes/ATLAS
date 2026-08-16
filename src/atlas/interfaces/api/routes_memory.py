"""Live Memory Dashboard — WebSocket stream + REST data endpoints.

WHY a dedicated module: memory data has its own shape (episodes, facts,
knowledge chunks, preferences) and its own real-time bus topic ("memory").
Mixing it into routes_trust.py (read-only projections) or routes_events.py
(orchestrator events) would conflate concerns.

WHY a dedicated ConnectionManager for memory: the existing global
ConnectionManager in websocket.py broadcasts every event from every topic
to every subscribed client. Memory clients only care about MemoryEvent
objects. Using a second manager keeps fan-out clean — no filter plumbing,
no risk of leaking task/safety events to the memory stream.

Endpoints
---------
WS   /ws/memory/live          Live MemoryEvent stream (all memory changes)
GET  /api/v1/memory/episodes  Recent episodes, filterable by task/kind/salience
GET  /api/v1/memory/facts     Semantic facts, filterable by kind/confidence
GET  /api/v1/memory/knowledge Knowledge document list + semantic search
GET  /api/v1/memory/preferences  All user preferences as key/value map
GET  /api/v1/memory/stats     Aggregate counts across all memory layers
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from atlas.infra.db import Database
from atlas.infra.logging import get_logger
from atlas.interfaces.api.websocket import ConnectionManager
from atlas.memory.cache import StatsCache

_log = get_logger("atlas.api.memory")

router = APIRouter()

# ---------------------------------------------------------------------------
# Dependency singleton (set by lifespan)
# ---------------------------------------------------------------------------


class MemoryStreamDeps:
    """Shared dependencies injected by create_app() lifespan."""

    def __init__(self, manager: ConnectionManager, db: Database, atlas: Any) -> None:
        self.manager = manager
        self.db = db
        self.atlas = atlas
        self.stats_cache = StatsCache(ttl=10.0)  # Phase 3.8: 10 s stats cache


_deps: MemoryStreamDeps | None = None


def set_dependencies(manager: ConnectionManager, db: Database, atlas: Any) -> None:
    """Called once from lifespan — makes deps available to route handlers."""
    global _deps
    _deps = MemoryStreamDeps(manager, db, atlas)


# ---------------------------------------------------------------------------
# Pydantic response shapes
# ---------------------------------------------------------------------------


class EpisodeOut(BaseModel):
    """A single episodic memory entry."""

    id: int | None = None
    correlation_id: str
    task_id: str | None = None
    ts: str
    kind: str
    role: str
    content: str
    tool: str | None = None
    outcome: str | None = None
    salience: float
    tokens: int
    embedding_id: str | None = None


class FactOut(BaseModel):
    """A single semantic fact."""

    id: str
    version: int
    text: str
    kind: str
    confidence: float
    salience: float
    created_ts: str
    updated_ts: str
    superseded_by: str | None = None


class KnowledgeDocOut(BaseModel):
    """A knowledge document with optional search score."""

    id: str
    title: str
    source_path: str
    source_type: str
    chunk_count: int
    indexed: bool
    created_ts: str
    score: float | None = None  # populated only in search results


class KnowledgeChunkOut(BaseModel):
    """A single knowledge chunk from search results."""

    chunk_id: str
    document_id: str
    document_title: str
    source_type: str
    content: str
    chunk_index: int
    total_chunks: int
    score: float


class MemoryStatsOut(BaseModel):
    """Aggregate counts across all memory layers."""

    episode_count: int
    fact_count: int
    document_count: int
    chunk_count: int
    preference_count: int
    # live indicator
    active_ws_clients: int


# ---------------------------------------------------------------------------
# WebSocket: /ws/memory/live
# ---------------------------------------------------------------------------


@router.websocket("/ws/memory/live")
async def memory_live_stream(websocket: WebSocket) -> None:
    """
    Live MemoryEvent stream — every memory write, retrieval, or update.

    Message types received by the client:
      {"kind": "memory.stored",            "memory_type": "episodic",   ...}
      {"kind": "memory.retrieved",         "memory_type": "episodic",   ...}
      {"kind": "memory.user_model_updated","memory_type": "user_model", ...}
      {"kind": "memory.fact_added",        "memory_type": "semantic",   ...}
      {"kind": "memory.knowledge_indexed", "memory_type": "knowledge",  ...}
      {"type": "ping"}            ← keepalive, reply with "pong"
      {"type": "snapshot"}        ← initial snapshot sent on connect

    The "_topic" field is always "memory" — useful if you fan this into a
    generic event handler alongside orchestrator events.
    """
    if _deps is None:
        await websocket.close(code=1011, reason="Server not initialised")
        return

    client_id = await _deps.manager.connect(websocket)
    _log.info("ws.memory_live_connected", event_type="websocket", client_id=client_id)

    try:
        # ── Initial snapshot: send current memory state so the client can
        #    render without waiting for the first live event ──────────────
        try:
            snapshot = await _build_snapshot(_deps.atlas)
            await websocket.send_json({"type": "snapshot", "_topic": "memory", **snapshot})
        except Exception as exc:
            _log.warning("ws.memory_snapshot_failed", event_type="websocket", client_id=client_id, error=str(exc))

        # ── Keep connection alive; broadcasts come from MemoryBroadcaster ─
        while True:
            try:
                data = await websocket.receive_text()
                if data == "pong":
                    continue
                if data == "close":
                    break
            except WebSocketDisconnect:
                break
            except Exception as exc:
                _log.error("ws.memory_receive_error", event_type="websocket", client_id=client_id, error=str(exc))
                break

    finally:
        await _deps.manager.disconnect(client_id)
        _log.info("ws.memory_live_disconnected", event_type="websocket", client_id=client_id)


async def _build_snapshot(atlas: Any) -> dict[str, Any]:
    """Build a lightweight initial-state snapshot for newly-connected clients."""
    try:
        recent_eps = await atlas.episodic.recent(limit=10)
        facts = await atlas.semantic.get_recent_facts(limit=10)
        prefs = await atlas.user_model.get_all_preferences()
        docs = await atlas.knowledge_store.list_documents(limit=5)

        return {
            "episode_count": len(recent_eps),
            "fact_count": len(facts),
            "document_count": len(docs),
            "preference_count": len(prefs),
            "recent_episode_kinds": [ep.kind.value for ep in recent_eps[:5]],
            "recent_fact_texts": [f.text[:80] for f in facts[:5]],
        }
    except Exception as exc:
        _log.warning("memory.snapshot_build_error", event_type="memory", error=str(exc))
        return {"episode_count": 0, "fact_count": 0, "document_count": 0, "preference_count": 0}


# ---------------------------------------------------------------------------
# REST: GET /api/v1/memory/episodes
# ---------------------------------------------------------------------------


@router.get("/api/v1/memory/episodes", response_model=list[EpisodeOut])
async def list_episodes(
    request: Request,
    task_id: str | None = Query(default=None),
    kind: str | None = Query(default=None),
    min_salience: float = Query(default=0.0, ge=0.0, le=1.0),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[EpisodeOut]:
    """
    Recent episodic memory entries.

    Supports filtering by task_id, event kind, and minimum salience.
    Ordered by salience DESC then timestamp DESC (highest-value first).
    Performance target: < 20 ms with Phase 3 indexes.
    """
    atlas = request.app.state.atlas

    from atlas.memory.types import EpisodeKind

    episodes = await atlas.episodic.search_similar(
        task_id=task_id,
        kind=EpisodeKind(kind) if kind else None,
        min_salience=min_salience,
        limit=limit,
    )

    return [
        EpisodeOut(
            id=ep.id,
            correlation_id=ep.correlation_id,
            task_id=ep.task_id,
            ts=ep.ts.isoformat(),
            kind=ep.kind.value,
            role=ep.role,
            content=ep.content,
            tool=ep.tool,
            outcome=ep.outcome,
            salience=ep.salience,
            tokens=ep.tokens,
            embedding_id=getattr(ep, "embedding_id", None),
        )
        for ep in episodes
    ]


# ---------------------------------------------------------------------------
# REST: GET /api/v1/memory/facts
# ---------------------------------------------------------------------------


@router.get("/api/v1/memory/facts", response_model=list[FactOut])
async def list_facts(
    request: Request,
    kind: str | None = Query(default=None),
    min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[FactOut]:
    """
    Semantic facts (distilled knowledge).

    Supports filtering by FactKind and minimum confidence threshold.
    Only non-superseded facts are returned.
    Performance target: < 30 ms.
    """
    atlas = request.app.state.atlas

    from atlas.memory.types import FactKind

    facts = await atlas.semantic.get_recent_facts(
        kind=FactKind(kind) if kind else None,
        min_confidence=min_confidence,
        limit=limit,
    )

    return [
        FactOut(
            id=f.id,
            version=f.version,
            text=f.text,
            kind=f.kind.value,
            confidence=f.confidence,
            salience=f.salience,
            created_ts=f.created_ts.isoformat(),
            updated_ts=f.updated_ts.isoformat(),
            superseded_by=f.superseded_by,
        )
        for f in facts
    ]


# ---------------------------------------------------------------------------
# REST: GET /api/v1/memory/knowledge
# ---------------------------------------------------------------------------


@router.get("/api/v1/memory/knowledge", response_model=list[KnowledgeDocOut])
async def list_knowledge_docs(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[KnowledgeDocOut]:
    """
    List all ingested knowledge documents.
    Ordered by ingestion time descending.
    """
    atlas = request.app.state.atlas
    docs = await atlas.knowledge_store.list_documents(limit=limit, offset=offset)

    return [
        KnowledgeDocOut(
            id=doc.id,
            title=doc.title,
            source_path=doc.source_path,
            source_type=doc.source_type,
            chunk_count=doc.chunk_count,
            indexed=doc.indexed,
            created_ts=doc.created_ts.isoformat(),
        )
        for doc in docs
    ]


@router.get("/api/v1/memory/knowledge/search", response_model=list[KnowledgeChunkOut])
async def search_knowledge(
    request: Request,
    q: str = Query(..., min_length=1, max_length=500),
    limit: int = Query(default=10, ge=1, le=50),
) -> list[KnowledgeChunkOut]:
    """
    Semantic search across all indexed knowledge chunks.
    Returns ranked chunks with relevance scores. < 100 ms target.
    """
    atlas = request.app.state.atlas
    results = await atlas.knowledge_store.search(q, limit=limit)

    return [
        KnowledgeChunkOut(
            chunk_id=r["chunk_id"],
            document_id=r["document_id"],
            document_title=r["document_title"],
            source_type=r["source_type"],
            content=r["content"],
            chunk_index=r["chunk_index"],
            total_chunks=r["total_chunks"],
            score=r["score"],
        )
        for r in results
    ]


# ---------------------------------------------------------------------------
# REST: GET /api/v1/memory/preferences
# ---------------------------------------------------------------------------


@router.get("/api/v1/memory/preferences")
async def list_preferences(request: Request) -> dict[str, str]:
    """
    All user model preferences as a flat key/value map.
    Served from in-memory cache — < 1 ms.
    """
    atlas = request.app.state.atlas
    result: dict[str, str] = await atlas.user_model.get_all_preferences()
    return result


# ---------------------------------------------------------------------------
# REST: GET /api/v1/memory/stats
# ---------------------------------------------------------------------------


@router.get("/api/v1/memory/stats", response_model=MemoryStatsOut)
async def memory_stats(request: Request) -> MemoryStatsOut:
    """
    Aggregate counts across all memory layers plus live WebSocket count.
    Used by the dashboard header cards. Results cached for 10 s.
    """
    atlas = request.app.state.atlas

    active_ws = _deps.manager.get_stats()["total_connections"] if _deps else 0

    # ── Cache check ──────────────────────────────────────────────────────
    if _deps:
        cached = await _deps.stats_cache.get()
        if cached is not None:
            return MemoryStatsOut(**cached, active_ws_clients=active_ws)

    # ── DB queries in parallel ───────────────────────────────────────────
    ep_count, fact_count, doc_count, chunk_count, prefs = await asyncio.gather(
        _count_episodes(atlas.db),
        _count_facts(atlas.db),
        _count_documents(atlas.db),
        _count_chunks(atlas.db),
        atlas.user_model.get_all_preferences(),
    )

    counts = {
        "episode_count": ep_count,
        "fact_count": fact_count,
        "document_count": doc_count,
        "chunk_count": chunk_count,
        "preference_count": len(prefs),
    }
    if _deps:
        await _deps.stats_cache.set(counts)

    return MemoryStatsOut(**counts, active_ws_clients=active_ws)


# ---------------------------------------------------------------------------
# Private DB helpers
# ---------------------------------------------------------------------------


async def _count_episodes(db: Database) -> int:
    cur = await db.conn.execute("SELECT COUNT(*) FROM episodes")
    row = await cur.fetchone()
    return int(row[0]) if row else 0


async def _count_facts(db: Database) -> int:
    cur = await db.conn.execute("SELECT COUNT(*) FROM semantic_facts WHERE superseded_by IS NULL")
    row = await cur.fetchone()
    return int(row[0]) if row else 0


async def _count_documents(db: Database) -> int:
    cur = await db.conn.execute("SELECT COUNT(*) FROM knowledge_documents")
    row = await cur.fetchone()
    return int(row[0]) if row else 0


async def _count_chunks(db: Database) -> int:
    cur = await db.conn.execute("SELECT COUNT(*) FROM knowledge_chunks")
    row = await cur.fetchone()
    return int(row[0]) if row else 0
