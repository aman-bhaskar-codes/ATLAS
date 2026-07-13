"""Tavily search provider — web corroboration, key from the Identity Platform."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from atlas.capabilities.domain.common import Provenance, SourceKind
from atlas.capabilities.domain.knowledge import KnowledgeItem
from atlas.capabilities.identity.errors import IdentityError
from atlas.capabilities.identity.platform import IdentityPlatform
from atlas.capabilities.providers.base import CapabilityRequest, RetryPolicy
from atlas.capabilities.registry.capability import Capability


class TavilySearchProvider:
    capability = Capability.KNOWLEDGE
    is_local = False
    requires_auth = True
    source_kind = "web"

    def __init__(self, identity: IdentityPlatform, credential_id: str,
                 timeout_s: float = 15.0) -> None:
        self.name = "tavily"
        self._identity = identity
        self._credential_id = credential_id
        self._client = httpx.AsyncClient(timeout=timeout_s)

    async def initialize(self) -> None: ...

    async def authenticate(self) -> None:
        await self._identity.get_usable_secret(self._credential_id)

    async def health(self) -> bool:
        try:
            await self._identity.get_usable_secret(self._credential_id)
            return True
        except Exception:
            return False

    async def search(self, query: str, *, limit: int) -> list[KnowledgeItem]:
        try:
            key = await self._identity.get_usable_secret(self._credential_id)
        except Exception as exc:
            raise IdentityError(f"tavily key unavailable: {exc}") from exc
            
        try:
            r = await self._client.post(
                "https://api.tavily.com/search",
                headers={"Content-Type": "application/json"},
                json={"api_key": key, "query": query, "max_results": limit, "include_answer": False}
            )
            r.raise_for_status()
            out: list[KnowledgeItem] = []
            for res in r.json().get("results", [])[:limit]:
                out.append(KnowledgeItem(
                    title=res.get("title", ""), snippet=res.get("content", "")[:500],
                    url=res.get("url"),
                    published=None,
                    provenance=Provenance(provider=self.name, source_kind=SourceKind.WEB,
                                          uri=res.get("url"), retrieved_ts=datetime.now(UTC))))
            return out
        except (httpx.HTTPError, Exception):
            return []

    async def execute(self, request: CapabilityRequest) -> Any:
        return await self.search(str(request.args.get("query", "")),
                                 limit=int(request.args.get("limit", 6)))

    def normalize(self, raw: Any) -> Any:
        return raw

    def retry_policy(self) -> RetryPolicy:
        return RetryPolicy(max_attempts=2)

    async def shutdown(self) -> None:
        await self._client.aclose()
