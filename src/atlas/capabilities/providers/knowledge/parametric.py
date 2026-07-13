"""Phase-5 model as a knowledge source. source_kind='model'. 

WHY: static facts ('what is a B-tree') need no external call — the model answers, 
cheapest live-free path. Routed only when the query is classified STATIC.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from atlas.capabilities.domain.common import Provenance, SourceKind
from atlas.capabilities.domain.knowledge import KnowledgeItem
from atlas.capabilities.providers.base import CapabilityRequest, RetryPolicy
from atlas.capabilities.registry.capability import Capability
from atlas.infra.ids import CorrelationId
from atlas.infra.types import ModelRequest
from atlas.intelligence.gateway import ModelGateway


class ParametricKnowledgeSource:
    name = "parametric"
    capability = Capability.KNOWLEDGE
    is_local = True     # Not fully 'local' if it uses cloud model, but it's internal
    requires_auth = False
    source_kind = "model"

    def __init__(self, gateway: ModelGateway) -> None:
        self._gw = gateway

    async def initialize(self) -> None: ...
    async def authenticate(self) -> None: ...
    async def health(self) -> bool: return True

    async def search(self, query: str, *, limit: int) -> list[KnowledgeItem]:
        try:
            resp = await self._gw.complete(ModelRequest(
                correlation_id=CorrelationId("parametric-lookup"),
                system="You are an expert knowledge base. Provide a concise, accurate answer.",
                prompt=query,
                max_tokens=600
            ))
            
            return [KnowledgeItem(
                title="Model parametric knowledge",
                snippet=resp.text,
                url="model:parametric",
                published=None,
                provenance=Provenance(provider=self.name, source_kind=SourceKind.MODEL,
                                      uri="model:parametric",
                                      retrieved_ts=datetime.now(UTC))
            )]
        except Exception:
            return []

    async def execute(self, request: CapabilityRequest) -> Any:
        return await self.search(str(request.args.get("query", "")),
                                 limit=int(request.args.get("limit", 6)))

    def normalize(self, raw: Any) -> Any:
        return raw

    def retry_policy(self) -> RetryPolicy:
        return RetryPolicy(max_attempts=1)

    async def shutdown(self) -> None: ...
