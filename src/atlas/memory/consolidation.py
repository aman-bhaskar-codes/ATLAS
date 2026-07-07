"""Consolidation — turn raw episodes into distilled, deduped knowledge.

WHY human-gated: auto-applying high-confidence, non-conflicting facts gives ~80%
of the 'it's learning me' feeling; anything conflicting or low-confidence becomes
a proposal you approve. This is the guardrail that keeps semantic memory clean.
WHY dedupe against existing facts: prevents the vector DB from filling with near-
duplicates — the #1 RAG-quality killer.
"""

from __future__ import annotations

import json

from atlas.infra.clock import Clock
from atlas.infra.db import Database
from atlas.infra.ids import IdGenerator
from atlas.infra.logging import get_logger
from atlas.infra.types import ModelRequest
from atlas.intelligence.gateway import ModelGateway
from atlas.memory.episodic import EpisodicMemory
from atlas.memory.semantic import SemanticMemory
from atlas.memory.types import FactKind

_log = get_logger("atlas.memory.consolidation")

_DISTILL_PROMPT = """You are the memory consolidator for a personal agent.
Given today's raw episodes, extract durable knowledge as JSON:
{"facts":[{"text":"...","kind":"preference|fact|skill|contact|project",
"confidence":0.0-1.0}], "user_model_updates":[{"section":"...","content":"..."}]}
Only extract things worth remembering long-term. Prefer few high-quality facts.
Episodes:
"""

_AUTO_APPLY_CONFIDENCE = 0.8
_DUP_SIMILARITY = 0.92


class Consolidator:
    def __init__(
        self, *, episodic: EpisodicMemory, semantic: SemanticMemory,
        gateway: ModelGateway, db: Database, ids: IdGenerator, clock: Clock,
    ) -> None:
        self._epi = episodic
        self._sem = semantic
        self._gw = gateway
        self._db = db
        self._ids = ids
        self._clock = clock

    async def run(self) -> dict[str, int]:
        episodes = await self._epi.unconsolidated(limit=500)
        if not episodes:
            return {"episodes": 0, "applied": 0, "proposed": 0}

        blob = "\n".join(f"[{e.kind.value}] {e.content}" for e in episodes)
        resp = await self._gw.complete(ModelRequest(
            correlation_id=self._ids.correlation_id(),
            system="Extract durable memory. Output ONLY JSON.",
            prompt=_DISTILL_PROMPT + blob,
            needs_deep_reasoning=True,  # thinking on; offline, no latency pressure
            max_tokens=1200,
        ))
        try:
            parsed = json.loads(self._extract_json(resp.text))
        except (json.JSONDecodeError, ValueError) as exc:
            _log.error("consolidation.parse_failed", event_type="memory", error=repr(exc))
            return {"episodes": len(episodes), "applied": 0, "proposed": 0}

        applied = proposed = 0
        source_ids = tuple(e.id for e in episodes if e.id is not None)

        for fact in parsed.get("facts", []):
            text = str(fact.get("text", "")).strip()
            if not text:
                continue
            conf = float(fact.get("confidence", 0.5))
            # dedupe: is this near-identical to an existing fact?
            existing = await self._sem.semantic_search(text, k=1)
            is_dup = existing and self._roughly_same(text, existing[0].text)
            if is_dup:
                continue
            if conf >= _AUTO_APPLY_CONFIDENCE:
                await self._sem.add_fact(
                    text, self._kind(fact.get("kind")), confidence=conf,
                    salience=0.5, sources=source_ids,
                )
                applied += 1
            else:
                await self._propose("new_fact", {"fact": fact, "sources": list(source_ids)})
                proposed += 1

        # user-model updates ALWAYS go to review (Tier-2: your identity)
        for um in parsed.get("user_model_updates", []):
            await self._propose("user_model", um)
            proposed += 1

        await self._epi.mark_consolidated([e.id for e in episodes if e.id is not None])
        _log.info("consolidation.done", event_type="memory",
                  episodes=len(episodes), applied=applied, proposed=proposed)
        return {"episodes": len(episodes), "applied": applied, "proposed": proposed}

    async def _propose(self, kind: str, payload: dict[str, object]) -> None:
        await self._db.conn.execute(
            "INSERT INTO consolidation_proposals(id, created_ts, kind, payload, status) "
            "VALUES (?,?,?,?, 'pending')",
            (self._ids.execution_id(), self._clock.now().isoformat(), kind, json.dumps(payload)),
        )
        await self._db.conn.commit()

    @staticmethod
    def _roughly_same(a: str, b: str) -> bool:
        # cheap token-overlap dup check; the vector hit already means semantically close
        sa, sb = set(a.lower().split()), set(b.lower().split())
        if not sa or not sb:
            return False
        return len(sa & sb) / len(sa | sb) >= 0.6

    @staticmethod
    def _kind(raw: object) -> FactKind:
        try:
            return FactKind(str(raw))
        except ValueError:
            return FactKind.FACT

    @staticmethod
    def _extract_json(text: str) -> str:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1:
            raise ValueError("no JSON object in model output")
        return text[start : end + 1]
