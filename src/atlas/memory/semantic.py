"""Semantic memory — distilled, versioned knowledge with real-time extraction.

WHY supersede instead of overwrite: 'I used to prefer X, now Y' must be
reconstructable. Updating a fact creates a new version and points the old one's
superseded_by at it. WHY embeddings via the gateway: bge-m3 locally, $0, and
the one embedding path is audited/metered like any model call.

Phase 3: Real-time fact extraction
- Extracts facts immediately from successful task completions
- Rule-based extraction for speed (< 5ms)
- Optional LLM-based extraction for complex patterns
- Auto-commit facts with confidence > 0.8
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from atlas.infra.clock import Clock
from atlas.infra.db import Database
from atlas.infra.ids import IdGenerator
from atlas.infra.logging import get_logger
from atlas.memory.embedder import Embedder
from atlas.memory.types import FactKind, SemanticFact
from atlas.memory.vectorstore import VectorStore

if TYPE_CHECKING:
    from atlas.infra.bus import MessageBus, Event

_log = get_logger("atlas.memory.semantic")


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
        self._bus: "MessageBus | None" = None

    def set_bus(self, bus: "MessageBus") -> None:
        """Connect to event bus for real-time fact extraction."""
        self._bus = bus
        # Subscribe to task completion events
        bus.subscribe("orchestrator", self._on_task_event)
        _log.info("semantic.bus_connected", event_type="memory")

    async def _on_task_event(self, event: "Event") -> None:
        """Extract facts in real-time from task events."""
        from atlas.orchestration.events import OrchestratorEvent
        
        if not isinstance(event, OrchestratorEvent):
            return
        
        # Only extract from completed tasks
        if event.kind != "task.completed":
            return
        
        try:
            # Quick rule-based extraction (< 5ms)
            facts = await self._extract_facts_fast(event)
            
            # Auto-commit facts with high confidence
            committed = 0
            for text, kind, confidence, salience in facts:
                if confidence >= 0.8:
                    fact_id = await self.add_fact(
                        text=text,
                        kind=kind,
                        confidence=confidence,
                        salience=salience,
                        sources=[]  # Could track episode IDs if needed
                    )
                    committed += 1
                    _log.debug(
                        "semantic.fact_extracted",
                        event_type="memory",
                        fact_id=fact_id,
                        kind=kind.value,
                        confidence=confidence
                    )
            
            if committed > 0:
                _log.info(
                    "semantic.facts_committed",
                    event_type="memory",
                    task_id=event.task_id,
                    count=committed
                )
                
        except Exception as exc:
            _log.error(
                "semantic.extraction_error",
                event_type="memory",
                task_id=event.task_id,
                error=str(exc)
            )

    async def _extract_facts_fast(
        self,
        event: "Event"
    ) -> list[tuple[str, FactKind, float, float]]:
        """
        Fast rule-based fact extraction (< 5ms).
        
        Returns: List of (text, kind, confidence, salience)
        """
        from atlas.orchestration.events import OrchestratorEvent
        
        if not isinstance(event, OrchestratorEvent):
            return []
        
        facts: list[tuple[str, FactKind, float, float]] = []
        metadata = event.metadata
        
        # Pattern 1: Tool usage patterns
        tool = metadata.get("tool")
        if tool and metadata.get("success"):
            fact_text = f"Successfully used {tool} tool"
            facts.append((fact_text, FactKind.SKILL, 0.85, 0.6))
        
        # Pattern 2: Error recovery (high value!)
        error = metadata.get("error")
        solution = metadata.get("solution") or metadata.get("recovery")
        if error and solution:
            fact_text = f"When encountering '{error[:100]}', solution: {solution[:100]}"
            facts.append((fact_text, FactKind.SKILL, 0.9, 0.9))
        
        # Pattern 3: User corrections (highest signal!)
        correction = metadata.get("correction")
        if correction:
            fact_text = f"User correction: {correction[:200]}"
            facts.append((fact_text, FactKind.PREFERENCE, 1.0, 1.0))
        
        # Pattern 4: Repeated tool preferences
        if metadata.get("tool_preference_detected"):
            fact_text = f"Prefer {metadata['tool_preference_detected']} for this task type"
            facts.append((fact_text, FactKind.PREFERENCE, 0.85, 0.7))
        
        # Pattern 5: Project/context info
        project = metadata.get("project") or metadata.get("context_project")
        if project:
            fact_text = f"Working on project: {project}"
            facts.append((fact_text, FactKind.PROJECT, 0.8, 0.5))
        
        return facts

    async def extract_facts_llm(
        self,
        task_summary: str,
        outcome: str,
        max_facts: int = 3
    ) -> list[tuple[str, FactKind, float, float]]:
        """
        Optional LLM-based fact extraction for complex patterns.
        
        Used for high-salience tasks where rule-based extraction isn't enough.
        Target: < 500ms with fast model (gpt-4o-mini).
        
        Returns: List of (text, kind, confidence, salience)
        """
        # TODO: Implement when intelligence gateway is available
        # For now, return empty list (rule-based is sufficient)
        _log.debug(
            "semantic.llm_extraction_skipped",
            event_type="memory",
            reason="Not yet implemented - rule-based sufficient"
        )
        return []

    async def get_recent_facts(
        self,
        kind: FactKind | None = None,
        limit: int = 20,
        min_confidence: float = 0.5
    ) -> list[SemanticFact]:
        """Get recent facts with optional filtering (< 30ms)."""
        conditions = ["superseded_by IS NULL"]
        params: list[str | float | int] = []
        
        if kind:
            conditions.append("kind = ?")
            params.append(kind.value)
        
        if min_confidence > 0:
            conditions.append("confidence >= ?")
            params.append(min_confidence)
        
        where_clause = " AND ".join(conditions)
        params.append(limit)
        
        cur = await self._db.conn.execute(
            f"""
            SELECT * FROM semantic_facts
            WHERE {where_clause}
            ORDER BY updated_ts DESC
            LIMIT ?
            """,
            params
        )
        
        return [self._row(r) for r in await cur.fetchall()]

    async def get_facts_by_confidence(
        self,
        min_confidence: float = 0.8,
        limit: int = 50
    ) -> list[SemanticFact]:
        """Get high-confidence facts for reliable retrieval (< 30ms)."""
        cur = await self._db.conn.execute(
            """
            SELECT * FROM semantic_facts
            WHERE superseded_by IS NULL
              AND confidence >= ?
            ORDER BY confidence DESC, salience DESC
            LIMIT ?
            """,
            (min_confidence, limit)
        )
        
        return [self._row(r) for r in await cur.fetchall()]

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
