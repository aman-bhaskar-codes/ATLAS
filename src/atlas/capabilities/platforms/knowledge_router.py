"""Knowledge router — static? memory? live? official-first?

ROUTING (your spec):
  static question           -> parametric (model)
  requires memory           -> memory retrieval
  requires live info        -> live providers, official-source-preferred
  sources disagree          -> collect evidence -> rank -> summarize -> confidence
WHY a cheap local classification: a fast local call labels intent; deterministic
freshness cues ('this week', 'latest', 'today') force LIVE regardless.
"""

from __future__ import annotations

import json

from atlas.capabilities.domain.knowledge import KnowledgeIntent, KnowledgeQuery
from atlas.infra.ids import CorrelationId
from atlas.infra.logging import get_logger
from atlas.infra.types import ModelRequest
from atlas.intelligence.gateway import ModelGateway

_log = get_logger("atlas.knowledge.router")

_LIVE_CUES = ("today", "this week", "latest", "recent", "now", "current", "just announced",
              "yesterday", "this month", "breaking")

_CLASSIFY = ("Classify how to answer this query. Output ONLY JSON: "
             '{"intent":"static|memory|live|mixed"}. static=timeless fact; '
             "memory=about the user's own past/data; live=needs current info.")


class KnowledgeRouter:
    def __init__(self, gateway: ModelGateway) -> None:
        self._gw = gateway

    async def classify(self, query: KnowledgeQuery, correlation_id: CorrelationId) -> KnowledgeIntent:
        low = query.text.lower()
        if any(c in low for c in _LIVE_CUES) or query.freshness_days is not None:
            return KnowledgeIntent.LIVE
        try:
            resp = await self._gw.complete(ModelRequest(
                correlation_id=correlation_id, system=_CLASSIFY, prompt=query.text,
                max_tokens=40, temperature=0.0))
            intent = json.loads(self._json(resp.text)).get("intent", "live")
            return KnowledgeIntent(intent)
        except Exception as exc:
            _log.warning("krouter.classify_failed", event_type="knowledge", error=repr(exc))
            return KnowledgeIntent.LIVE   # fail toward gathering evidence, not guessing

    @staticmethod
    def _json(text: str) -> str:
        s, e = text.find("{"), text.rfind("}")
        if s == -1 or e == -1:
            raise ValueError("no json")
        return text[s : e + 1]
