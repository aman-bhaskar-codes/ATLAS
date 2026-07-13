"""Phase-3 retrieval as a knowledge source. source_kind='local'. 

WHY: 'have I already learned this?' is the cheapest, most private source — checked first.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from atlas.capabilities.domain.common import Provenance, SourceKind
from atlas.capabilities.domain.knowledge import KnowledgeItem
from atlas.capabilities.providers.base import CapabilityRequest, RetryPolicy
from atlas.capabilities.registry.capability import Capability
from atlas.memory.retrieval import Retriever


class MemoryKnowledgeSource:
    name = "memory_source"
    capability = Capability.KNOWLEDGE
    is_local = True
    requires_auth = False
    source_kind = "local"

    def __init__(self, retriever: Retriever) -> None:
        self._retriever = retriever

    async def initialize(self) -> None: ...
    async def authenticate(self) -> None: ...
    async def health(self) -> bool: return True

    async def search(self, query: str, *, limit: int) -> list[KnowledgeItem]:
        # Perform retrieval
        try:
            ctx = await self._retriever.retrieve(query)
        except Exception:
            return []
            
        items: list[KnowledgeItem] = []
        
        # Pull facts from semantic memory
        for fact in ctx.facts[:limit]:
            items.append(KnowledgeItem(
                title=f"Memory Fact ({fact.kind.value})",
                snippet=fact.text,
                url=f"memory:fact:{fact.id}",
                published=fact.created_ts,
                provenance=Provenance(provider=self.name, source_kind=SourceKind.LOCAL,
                                      uri=f"memory:fact:{fact.id}",
                                      retrieved_ts=datetime.now(UTC))))
                                      
        # Optionally pull episodes as well, up to the limit
        for ep in ctx.recent_episodes[:max(0, limit - len(items))]:
            items.append(KnowledgeItem(
                title=f"Past Episode ({ep.kind.value})",
                snippet=ep.content[:500],
                url=f"memory:episode:{ep.id}",
                published=ep.ts,
                provenance=Provenance(provider=self.name, source_kind=SourceKind.LOCAL,
                                      uri=f"memory:episode:{ep.id}",
                                      retrieved_ts=datetime.now(UTC))))
                                      
        return items[:limit]

    async def execute(self, request: CapabilityRequest) -> Any:
        return await self.search(str(request.args.get("query", "")),
                                 limit=int(request.args.get("limit", 6)))

    def normalize(self, raw: Any) -> Any:
        return raw

    def retry_policy(self) -> RetryPolicy:
        return RetryPolicy(max_attempts=1)

    async def shutdown(self) -> None: ...
