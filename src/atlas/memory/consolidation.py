"""Consolidation — turn raw episodes into distilled, deduped knowledge.

WHY human-gated: auto-applying high-confidence, non-conflicting facts gives ~80%
of the 'it's learning me' feeling; anything conflicting or low-confidence becomes
a proposal you approve. This is the guardrail that keeps semantic memory clean.
WHY dedupe against existing facts: prevents the vector DB from filling with near-
duplicates — the #1 RAG-quality killer.

TWO GATES. The single most important property of this module is that the model
never decides what it is allowed to learn from.

* **Gate 1 — deterministic, before the model sees anything.** Candidates come
  from :meth:`EpisodicMemory.promotion_candidates`, which filters provenance in
  SQL. Untrusted (web pages, tool output) and system chatter are excluded from
  the *prompt*, not from the result. WHY that ordering matters: a filter applied
  to the model's output is advice, and a sufficiently confident injected sentence
  talks its way past advice. A filter applied to the input is arithmetic.
* **Gate 2 — validation, after the model, before the write.** The merged curated
  document is checked for the failure that actually happens in practice: a merge
  that silently drops most of what was already there. A rejected merge degrades
  to appending the candidate line, because losing a new fact is a smaller loss
  than losing the document.

The write itself is a compare-and-swap on the hash captured *before* the model
call, so a live turn that edited the curated tier during the (slow, bounded)
model call wins and this sweep aborts rather than clobbering it.
"""

from __future__ import annotations

import json

from atlas.infra.clock import Clock
from atlas.infra.db import Database
from atlas.infra.ids import IdGenerator
from atlas.infra.logging import get_logger
from atlas.infra.types import ModelCapability, ModelRequest
from atlas.intelligence.gateway import ModelGateway
from atlas.memory.curated import MEMORY_KEY, CuratedMemory
from atlas.memory.episodic import EpisodicMemory
from atlas.memory.semantic import SemanticMemory
from atlas.memory.types import Episode, FactKind

_log = get_logger("atlas.memory.consolidation")

_DISTILL_PROMPT = """You are the memory consolidator for a personal agent.
Given today's raw episodes, extract durable knowledge as JSON:
{"facts":[{"text":"...","kind":"preference|fact|skill|contact|project",
"confidence":0.0-1.0}], "user_model_updates":[{"section":"...","content":"..."}],
"curated_memory":"<the FULL merged MEMORY document, markdown>"}
Only extract things worth remembering long-term. Prefer few high-quality facts.
For "curated_memory": start from CURRENT MEMORY below, keep every line that is
still true, merge in what is new, and delete nothing that has not been
contradicted. Output the whole document, not a diff.
"""

_AUTO_APPLY_CONFIDENCE = 0.8
_DUP_SIMILARITY = 0.92

#: A merged curated document may not fall below this fraction of the pre-image
#: length. WHY a ratio and not a diff: the realistic failure is a model that
#: summarises instead of merging and returns three lines where there were forty.
#: A length floor catches that without needing to understand the content, and it
#: cannot be argued with. Growth is unbounded — adding is safe, dropping is not.
_MIN_MERGE_RETENTION = 0.6

#: Absolute ceiling on the curated document. The tier's whole value is that it is
#: cheap enough to load on every turn; without a cap, consolidation would slowly
#: turn it back into the large context it exists to avoid.
_MAX_CURATED_CHARS = 20_000


class Consolidator:
    def __init__(
        self,
        *,
        episodic: EpisodicMemory,
        semantic: SemanticMemory,
        gateway: ModelGateway,
        db: Database,
        ids: IdGenerator,
        clock: Clock,
        curated: CuratedMemory | None = None,
    ) -> None:
        self._epi = episodic
        self._sem = semantic
        self._gw = gateway
        self._db = db
        self._ids = ids
        self._clock = clock
        self._curated = curated

    async def run(self) -> dict[str, int]:
        # ── Gate 1: deterministic provenance filter, pre-model ───────────
        episodes = await self._epi.promotion_candidates(limit=200)
        # Belt and braces: the SQL is the gate, this mirrors it in Python so a
        # future edit to the query cannot quietly widen what a model may read.
        eligible = [e for e in episodes if e.promotable]
        # Everything unconsolidated gets marked consumed at the end, including
        # the rows Gate 1 excluded — otherwise untrusted episodes are rescanned
        # on every sweep forever.
        all_pending = await self._epi.unconsolidated(limit=500)
        if not eligible:
            await self._mark(all_pending)
            _log.info(
                "consolidation.no_candidates",
                event_type="memory",
                pending=len(all_pending),
                excluded=len(all_pending),
            )
            return {"episodes": 0, "applied": 0, "proposed": 0, "excluded": len(all_pending)}

        excluded = max(0, len(all_pending) - len(eligible))

        # Captured BEFORE the model call — this is the compare-and-swap token.
        pre_doc = await self._curated.create_if_absent(MEMORY_KEY) if self._curated else None
        current_memory = pre_doc.content if pre_doc else ""

        blob = "\n".join(f"[{e.kind.value}] {e.content}" for e in eligible)
        prompt = f"{_DISTILL_PROMPT}\nCURRENT MEMORY:\n{current_memory}\n\nEpisodes:\n{blob}"
        resp = await self._gw.complete(
            ModelRequest(
                correlation_id=self._ids.correlation_id(),
                system="Extract durable memory. Output ONLY JSON.",
                prompt=prompt,
                required_capabilities=frozenset(
                    {
                        ModelCapability.REASONING,
                        ModelCapability.SUMMARIZATION,
                        ModelCapability.JSON_GENERATION,
                    }
                ),
                needs_deep_reasoning=True,  # thinking on; offline, no latency pressure
                max_tokens=1200,
            )
        )
        try:
            parsed = json.loads(self._extract_json(resp.text))
        except (json.JSONDecodeError, ValueError) as exc:
            _log.error("consolidation.parse_failed", event_type="memory", error=repr(exc))
            # Do NOT mark consumed: a parse failure is a transient model problem,
            # and these episodes deserve another sweep. Contrast with Gate 1
            # exclusions, which will never become eligible.
            return {"episodes": len(eligible), "applied": 0, "proposed": 0, "excluded": excluded}

        applied = proposed = 0
        source_ids = tuple(e.id for e in eligible if e.id is not None)
        top_fact = ""

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
                    text,
                    self._kind(fact.get("kind")),
                    confidence=conf,
                    salience=0.5,
                    sources=source_ids,
                )
                applied += 1
                if not top_fact:
                    top_fact = text
            else:
                await self._propose("new_fact", {"fact": fact, "sources": list(source_ids)})
                proposed += 1

        # user-model updates ALWAYS go to review (Tier-2: your identity)
        for um in parsed.get("user_model_updates", []):
            await self._propose("user_model", um)
            proposed += 1

        # ── Gate 2: validate, then compare-and-swap the curated tier ─────
        if pre_doc is not None:
            await self._write_curated(
                merged=str(parsed.get("curated_memory") or ""),
                pre_image=pre_doc.content,
                expected_hash=pre_doc.content_hash,
                fallback_line=top_fact,
            )

        await self._mark(all_pending)
        _log.info(
            "consolidation.done",
            event_type="memory",
            episodes=len(eligible),
            applied=applied,
            proposed=proposed,
            excluded=excluded,
        )
        return {"episodes": len(eligible), "applied": applied, "proposed": proposed, "excluded": excluded}

    async def _write_curated(
        self,
        *,
        merged: str,
        pre_image: str,
        expected_hash: str,
        fallback_line: str,
    ) -> bool:
        """Validate the merge and swap it in; degrade to an append on any doubt."""
        if self._curated is None:  # pragma: no cover — guarded by the caller
            return False
        reason = self._merge_rejection(merged, pre_image)
        if reason is None:
            if await self._curated.swap(MEMORY_KEY, new_content=merged.strip() + "\n", expected_hash=expected_hash):
                return True
            reason = "cas_conflict"

        _log.warning("consolidation.merge_rejected", event_type="memory", reason=reason)
        if not fallback_line:
            return False
        # Append is the designated degradation: it can add a duplicate line, but
        # it can never remove one.
        return await self._curated.append(MEMORY_KEY, f"- {fallback_line}")

    @staticmethod
    def _merge_rejection(merged: str, pre_image: str) -> str | None:
        """Why this merged document must not be written, or ``None`` if it may."""
        body = merged.strip()
        if not body:
            return "empty"
        if len(body) > _MAX_CURATED_CHARS:
            return "too_large"
        floor = len(pre_image.strip()) * _MIN_MERGE_RETENTION
        if len(body) < floor:
            return "lost_content"
        return None

    async def _mark(self, episodes: list[Episode]) -> None:
        await self._epi.mark_consolidated([e.id for e in episodes if e.id is not None])

    async def _propose(self, kind: str, payload: dict[str, object]) -> None:
        await self._db.conn.execute(
            "INSERT INTO consolidation_proposals(id, created_ts, kind, payload, status) VALUES (?,?,?,?, 'pending')",
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
