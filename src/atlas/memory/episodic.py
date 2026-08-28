"""Episodic memory — the raw log with real-time streaming.

WHY salience on write: corrections (user overrides the agent) are the highest-
signal events we own, so they're written with high salience and are the last to
be pruned. WHY `consolidated` flag: we NEVER prune an episode that hasn't been
distilled into semantic memory yet — no data loss before learning from it.

PHASE 3: Real-time streaming architecture
- Instant writes (< 10ms) on event bus
- Async embedding (doesn't block)
- Fast retrieval with indexes
- WebSocket broadcast for live updates
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from atlas.infra.clock import Clock
from atlas.infra.db import Database
from atlas.infra.logging import get_logger
from atlas.infra.tasks import spawn
from atlas.memory.types import Episode, EpisodeKind, OriginClass, SessionKind

if TYPE_CHECKING:
    from atlas.infra.bus import Event, MessageBus
    from atlas.memory.embedder import EmbeddingWorker

_log = get_logger("atlas.memory.episodic")


class EpisodicMemory:
    """Real-time episodic memory with instant writes and async embedding."""

    def __init__(
        self,
        db: Database,
        clock: Clock,
        embedding_worker: EmbeddingWorker | None = None,
    ) -> None:
        self._db = db
        self._clock = clock
        self._embedding_worker = embedding_worker
        self._bus: MessageBus | None = None

    def set_bus(self, bus: MessageBus) -> None:
        """Connect to event bus for real-time streaming."""
        self._bus = bus
        # Subscribe to all orchestrator events
        bus.subscribe("orchestrator", self._on_orchestrator_event)
        _log.info("episodic.bus_connected", event_type="memory")

    async def _on_orchestrator_event(self, event: Event) -> None:
        """Real-time event handler - writes episode instantly (< 10ms)."""
        # Duck-type check: OrchestratorEvent has task_id, state, kind, metadata attributes
        if not (hasattr(event, "task_id") and hasattr(event, "kind") and hasattr(event, "state")):
            return

        try:
            # Calculate salience based on event kind (fast, rule-based)
            salience = self._calculate_salience(event)

            # Access OrchestratorEvent-specific fields safely via getattr
            event_metadata: dict[str, object] = getattr(event, "metadata", {})
            event_task_id: str | None = getattr(event, "task_id", None)
            event_kind: str = getattr(event, "kind", "unknown")

            # Extract episode data
            episode = Episode(
                correlation_id=event.correlation_id,
                task_id=event_task_id,
                step=0,
                ts=self._clock.now(),
                kind=self._map_event_kind(event_kind),
                role="agent",
                content=self._extract_content(event),
                tool=str(event_metadata.get("tool")) if event_metadata.get("tool") else None,
                outcome=str(event_metadata.get("outcome")) if event_metadata.get("outcome") else None,
                salience=salience,
                tokens=int(str(event_metadata.get("tokens", 0) or 0)),
                origin_class=self._classify_origin(event_kind, event_metadata),
                session_kind=self._coerce_session_kind(event_metadata.get("session_kind")),
            )

            # CRITICAL: Instant write to DB (< 10ms)
            episode_id = await self.record(episode)

            # Async: Queue for embedding (doesn't block)
            if self._embedding_worker and episode_id > 0:
                spawn(
                    self._embedding_worker.embed_episode(episode_id, episode.content),
                    name=f"embed-episode-{episode_id}",
                )

            _log.debug(
                "episodic.recorded",
                event_type="memory",
                episode_id=episode_id,
                kind=episode.kind.value,
                salience=salience,
            )

        except Exception as exc:
            _log.error(
                "episodic.record_error",
                event_type="memory",
                error=str(exc),
                event_kind=event.kind,
            )

    def _calculate_salience(self, event: Event) -> float:
        """Fast, rule-based salience scoring (no LLM call)."""
        # Duck-type: OrchestratorEvent fields
        if not hasattr(event, "kind"):
            return 0.3

        kind = event.kind
        metadata = getattr(event, "metadata", {})

        # High salience events (learn from these!)
        if kind == "task.failed":
            return 0.9
        if metadata.get("requires_approval"):
            return 0.8
        if metadata.get("error"):
            return 0.8
        if kind in ("approval.denied", "safety.blocked"):
            return 0.85

        # Medium salience
        if kind in ("tool.completed", "plan.completed"):
            return 0.5
        if kind in ("tool.failed", "validation.failed"):
            return 0.7

        # Low salience (informational)
        if kind in ("thought.reasoning", "plan.started"):
            return 0.3

        # Default
        return 0.4

    def _map_event_kind(self, orchestrator_kind: str) -> EpisodeKind:
        """Map orchestrator event kinds to episode kinds."""
        if "tool" in orchestrator_kind or "action" in orchestrator_kind:
            return EpisodeKind.ACTION
        if "observation" in orchestrator_kind or "result" in orchestrator_kind:
            return EpisodeKind.OBSERVATION
        return EpisodeKind.MESSAGE

    def _classify_origin(self, event_kind: str, metadata: dict[str, object]) -> OriginClass:
        """Provenance for an orchestrator-authored episode.

        The distinction that matters is whether the *text* came from ATLAS's own
        reasoning or from something a tool fetched. A tool result is data from
        outside the trust boundary — a web page, a file, another service — so it
        is ``UNTRUSTED`` even though ATLAS is what wrote the row. Getting this
        backwards is how a prompt-injected sentence ends up in the curated tier
        looking like something ATLAS concluded.
        """
        if metadata.get("tool") or "tool" in event_kind or "observation" in event_kind:
            return OriginClass.UNTRUSTED
        return OriginClass.AGENT

    @staticmethod
    def _coerce_session_kind(raw: object) -> SessionKind:
        """Never let an unrecognised ``session_kind`` cost us the episode.

        The caller-supplied value is metadata, so it can be anything. Falling
        back to ``INTERACTIVE`` is the same conservative default the column has,
        and losing the row entirely over a typo would be a much worse failure
        than mislabelling its session.
        """
        try:
            return SessionKind(str(raw)) if raw else SessionKind.INTERACTIVE
        except ValueError:
            return SessionKind.INTERACTIVE

    def _extract_content(self, event: Event) -> str:
        """Extract meaningful content from event for episode storage."""
        metadata = getattr(event, "metadata", {})
        state = getattr(event, "state", "unknown")
        kind = getattr(event, "kind", "unknown")

        # Prefer summary, then content, then reasoning
        if "summary" in metadata:
            return str(metadata["summary"])
        if "content" in metadata:
            return str(metadata["content"])
        if "reasoning" in metadata:
            return str(metadata["reasoning"])
        if "message" in metadata:
            return str(metadata["message"])

        # Fallback: event kind as content
        return f"{kind} (state: {state})"

    async def record(self, ep: Episode) -> int:
        """Write episode to DB instantly (< 10ms). Returns episode ID.

        Provenance travels with the write and is never inferred later: the
        caller knows whether this text came from the owner, from ATLAS, or off a
        web page, and that knowledge is unrecoverable once the row exists.
        """
        cur = await self._db.conn.execute(
            "INSERT INTO episodes(correlation_id, task_id, step, ts, kind, role, "
            "content, tool, outcome, salience, consolidated, tokens, "
            "origin_class, session_kind, importance, trigger_hint) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,0,?,?,?,?,?)",
            (
                ep.correlation_id,
                ep.task_id,
                ep.step,
                ep.ts.isoformat(),
                ep.kind.value,
                ep.role,
                ep.content,
                ep.tool,
                ep.outcome,
                ep.salience,
                ep.tokens,
                ep.origin_class.value,
                ep.session_kind.value,
                ep.importance,
                ep.trigger_hint,
            ),
        )
        await self._db.conn.commit()
        episode_id = int(cur.lastrowid) if cur.lastrowid is not None else -1

        # Emit memory event for WebSocket broadcast — use MemoryBusEvent from infra
        # so atlas.memory does not import atlas.orchestration (layer boundary)
        if self._bus and episode_id > 0:
            from atlas.infra.bus import MemoryBusEvent

            spawn(
                self._bus.publish(
                    "memory",
                    MemoryBusEvent(
                        correlation_id=ep.correlation_id,
                        task_id=ep.task_id or "unknown",
                        kind="memory.stored",
                        memory_type="episodic",
                        count=1,
                        items=[f"Episode {episode_id}: {ep.kind.value}"],
                        metadata={"salience": ep.salience},
                    ),
                ),
                name=f"publish-episode-stored-{episode_id}",
            )

        return episode_id

    async def record_correction(self, correlation_id: str, content: str) -> int:
        """Corrections get max salience — this is how the agent learns you.

        Also the one write path that is unambiguously ``OWNER``: a correction is
        the human overriding ATLAS, so it is both the highest-signal episode we
        own and the only kind we can attribute to the user without inferring it.
        """
        return await self.record(
            Episode(
                correlation_id=correlation_id,
                ts=self._clock.now(),
                kind=EpisodeKind.CORRECTION,
                role="user",
                content=content,
                salience=1.0,
                origin_class=OriginClass.OWNER,
                importance=9,
            )
        )

    async def recent(self, limit: int = 50) -> list[Episode]:
        """Get recent episodes (< 10ms with index)."""
        cur = await self._db.conn.execute("SELECT * FROM episodes ORDER BY id DESC LIMIT ?", (limit,))
        rows = list(await cur.fetchall())
        return [self._row(r) for r in reversed(rows)]

    async def search_similar(
        self,
        task_id: str | None = None,
        kind: EpisodeKind | None = None,
        min_salience: float = 0.0,
        limit: int = 10,
    ) -> list[Episode]:
        """Fast retrieval with filters (< 50ms with indexes)."""
        conditions = []
        params: list[str | float] = []

        if task_id:
            conditions.append("task_id = ?")
            params.append(task_id)

        if kind:
            conditions.append("kind = ?")
            params.append(kind.value)

        if min_salience > 0:
            conditions.append("salience >= ?")
            params.append(min_salience)

        where_clause = " AND ".join(conditions) if conditions else "1=1"
        params.append(limit)

        # Optimized query: Uses idx_episodes_task_salience
        cur = await self._db.conn.execute(
            f"""
            SELECT * FROM episodes
            WHERE {where_clause}
            ORDER BY salience DESC, ts DESC
            LIMIT ?
            """,
            tuple(params),
        )

        return [self._row(r) for r in await cur.fetchall()]

    async def get_by_task(self, task_id: str, limit: int = 100) -> list[Episode]:
        """Get all episodes for a specific task (< 20ms with index)."""
        cur = await self._db.conn.execute(
            """
            SELECT * FROM episodes
            WHERE task_id = ?
            ORDER BY id ASC
            LIMIT ?
            """,
            (task_id, limit),
        )
        return [self._row(r) for r in await cur.fetchall()]

    async def get_by_correlation(self, correlation_id: str, limit: int = 100) -> list[Episode]:
        """Get all episodes for a correlation ID (< 20ms with index)."""
        cur = await self._db.conn.execute(
            """
            SELECT * FROM episodes
            WHERE correlation_id = ?
            ORDER BY id ASC
            LIMIT ?
            """,
            (correlation_id, limit),
        )
        return [self._row(r) for r in await cur.fetchall()]

    async def get_high_salience(self, limit: int = 20, min_salience: float = 0.7) -> list[Episode]:
        """Get most important episodes for learning (< 30ms)."""
        cur = await self._db.conn.execute(
            """
            SELECT * FROM episodes
            WHERE salience >= ?
            ORDER BY salience DESC, ts DESC
            LIMIT ?
            """,
            (min_salience, limit),
        )
        return [self._row(r) for r in await cur.fetchall()]

    async def semantic_search(
        self,
        query: str,
        limit: int = 10,
        min_salience: float = 0.0,
    ) -> list[Episode]:
        """Semantic search using vector embeddings (< 100ms with ChromaDB)."""
        if not self._embedding_worker:
            # Fallback to keyword search if no embeddings
            return await self.keyword_search([query], limit=limit)

        # Get query embedding
        query_embedding = await self._embedding_worker._embedder.embed(query)

        # Search vector store
        hits = await self._embedding_worker._vector_store.search_episodes(
            query_embedding,
            k=limit * 2,  # Over-fetch for filtering
        )

        # Extract episode IDs from hits
        episode_ids = []
        for hit in hits:
            if hit.ref.startswith("ep_"):
                try:
                    ep_id = int(hit.ref[3:])  # Remove "ep_" prefix
                    episode_ids.append(ep_id)
                except ValueError:
                    continue

        if not episode_ids:
            return []

        # Fetch full episodes from DB with salience filter
        placeholders = ",".join("?" * len(episode_ids))
        conditions = [f"id IN ({placeholders})"]
        params: list[int | float] = list(episode_ids)

        if min_salience > 0:
            conditions.append("salience >= ?")
            params.append(min_salience)

        where_clause = " AND ".join(conditions)

        cur = await self._db.conn.execute(
            f"""
            SELECT * FROM episodes
            WHERE {where_clause}
            ORDER BY salience DESC
            LIMIT ?
            """,
            [*params, limit],
        )

        return [self._row(r) for r in await cur.fetchall()]

    async def unconsolidated(self, limit: int = 500) -> list[Episode]:
        cur = await self._db.conn.execute("SELECT * FROM episodes WHERE consolidated=0 ORDER BY id LIMIT ?", (limit,))
        return [self._row(r) for r in await cur.fetchall()]

    async def promotion_candidates(self, limit: int = 200) -> list[Episode]:
        """Unconsolidated episodes that are *eligible* to become durable memory.

        The provenance filter lives in SQL, not in the caller, because this is
        the query that decides what a model is allowed to read during
        consolidation. If the gate were a Python ``if`` the caller could forget
        it; here, forgetting is impossible. Backed by the partial index
        ``idx_ep_promotion_candidates``.

        Note this is deliberately *narrower* than :meth:`unconsolidated`, which
        still returns everything — pruning and bookkeeping need the full set.
        """
        cur = await self._db.conn.execute(
            "SELECT * FROM episodes "
            "WHERE consolidated = 0 "
            "  AND origin_class IN (?,?) "
            "  AND session_kind = ? "
            "ORDER BY salience DESC, id ASC "
            "LIMIT ?",
            (
                OriginClass.OWNER.value,
                OriginClass.AGENT.value,
                SessionKind.INTERACTIVE.value,
                limit,
            ),
        )
        return [self._row(r) for r in await cur.fetchall()]

    async def keyword_search(self, terms: list[str], limit: int = 20) -> list[Episode]:
        """Sparse retrieval over episodic content (exact names/paths dense misses)."""
        if not terms:
            return []
        like = " OR ".join(["content LIKE ?" for _ in terms])
        params = [f"%{t}%" for t in terms] + [limit]
        cur = await self._db.conn.execute(
            f"SELECT * FROM episodes WHERE {like} ORDER BY salience DESC, id DESC LIMIT ?",
            params,
        )
        return [self._row(r) for r in await cur.fetchall()]

    async def mark_consolidated(self, ids: list[int]) -> None:
        if not ids:
            return
        qs = ",".join("?" for _ in ids)
        await self._db.conn.execute(f"UPDATE episodes SET consolidated=1 WHERE id IN ({qs})", ids)
        await self._db.conn.commit()

    @staticmethod
    def _row(r: object) -> Episode:
        d = dict(r)  # type: ignore[call-overload]
        return Episode(
            id=d["id"],
            correlation_id=d["correlation_id"],
            task_id=d["task_id"],
            step=d["step"],
            ts=datetime.fromisoformat(d["ts"]),
            kind=EpisodeKind(d["kind"]),
            role=d["role"],
            content=d["content"],
            tool=d["tool"],
            outcome=d["outcome"],
            salience=d["salience"],
            tokens=d["tokens"],
            # .get with a conservative default rather than [] — a row read from a
            # connection that predates migration 029 must still hydrate, and the
            # default must be the non-privileged one.
            origin_class=OriginClass(d.get("origin_class") or OriginClass.AGENT.value),
            session_kind=SessionKind(d.get("session_kind") or SessionKind.INTERACTIVE.value),
            importance=d.get("importance"),
            trigger_hint=d.get("trigger_hint"),
        )
