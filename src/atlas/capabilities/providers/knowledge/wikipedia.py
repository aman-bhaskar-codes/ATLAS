"""Wikipedia REST summary provider. source_kind='official' (encyclopedic)."""

from __future__ import annotations

import urllib.parse
from datetime import UTC, datetime
from typing import Any

import httpx

from atlas.capabilities.domain.common import Provenance, SourceKind
from atlas.capabilities.domain.knowledge import KnowledgeItem
from atlas.capabilities.providers.base import CapabilityRequest, RetryPolicy
from atlas.capabilities.registry.capability import Capability


class WikipediaProvider:
    name = "wikipedia"
    capability = Capability.KNOWLEDGE
    is_local = False
    requires_auth = False
    source_kind = "official"

    def __init__(self, timeout_s: float = 15.0) -> None:
        self._client = httpx.AsyncClient(timeout=timeout_s, follow_redirects=True)

    async def initialize(self) -> None: ...
    async def authenticate(self) -> None: ...

    async def health(self) -> bool:
        try:
            r = await self._client.get("https://en.wikipedia.org/w/rest.php/v1/page/Main_Page")
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def search(self, query: str, *, limit: int) -> list[KnowledgeItem]:
        try:
            # 1. Search for title
            r = await self._client.get(f"https://en.wikipedia.org/w/rest.php/v1/search/page?q={urllib.parse.quote(query)}&limit={limit}")
            r.raise_for_status()
            results = r.json().get("pages", [])
            
            items: list[KnowledgeItem] = []
            for res in results[:limit]:
                title = res.get("title")
                if not title:
                    continue
                # 2. Get extract
                r2 = await self._client.get(f"https://en.wikipedia.org/w/api.php?action=query&prop=extracts&exintro&explaintext&format=json&titles={urllib.parse.quote(title)}")
                pages = r2.json().get("query", {}).get("pages", {})
                extract = ""
                for page_id in pages:
                    extract = pages[page_id].get("extract", "")
                    
                items.append(KnowledgeItem(
                    title=f"Wikipedia: {title}",
                    snippet=extract[:500] if extract else res.get("description", ""),
                    url=f"https://en.wikipedia.org/wiki/{urllib.parse.quote(title)}",
                    published=None,
                    provenance=Provenance(provider=self.name, source_kind=SourceKind.OFFICIAL,
                                          uri=f"wikipedia:{title}",
                                          retrieved_ts=datetime.now(UTC))))
            return items
        except (httpx.HTTPError, Exception):
            return []

    async def execute(self, request: CapabilityRequest) -> Any:
        return await self.search(str(request.args.get("query", "")),
                                 limit=int(request.args.get("limit", 6)))

    def normalize(self, raw: Any) -> Any:
        return raw

    def retry_policy(self) -> RetryPolicy:
        return RetryPolicy(max_attempts=2, base_backoff_s=0.5)

    async def shutdown(self) -> None:
        await self._client.aclose()
