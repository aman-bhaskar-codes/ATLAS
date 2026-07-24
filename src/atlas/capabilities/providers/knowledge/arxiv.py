"""arXiv Atom API. source_kind='official' (research)."""

from __future__ import annotations

import urllib.parse
from datetime import UTC, datetime
from typing import Any

import feedparser  # type: ignore
import httpx

from atlas.capabilities.domain.common import Provenance, SourceKind
from atlas.capabilities.domain.knowledge import KnowledgeItem
from atlas.capabilities.providers.base import CapabilityRequest, RetryPolicy
from atlas.capabilities.registry.capability import Capability


class ArxivProvider:
    name = "arxiv"
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
            r = await self._client.get("http://export.arxiv.org/api/query?search_query=all:electron&max_results=1")
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def search(self, query: str, *, limit: int) -> list[KnowledgeItem]:
        try:
            r = await self._client.get(
                f"http://export.arxiv.org/api/query?search_query=all:{urllib.parse.quote(query)}&sortBy=submittedDate&sortOrder=descending&max_results={limit}")
            r.raise_for_status()
            parsed = feedparser.parse(r.text)
            
            items: list[KnowledgeItem] = []
            for entry in parsed.entries[:limit]:
                import time
                st = getattr(entry, "published_parsed", None)
                pub = datetime.fromtimestamp(time.mktime(st), tz=UTC) if st else None
                
                items.append(KnowledgeItem(
                    title=str(getattr(entry, "title", "")).replace("\n", " "),
                    snippet=str(getattr(entry, "summary", ""))[:500].replace("\n", " "),
                    url=getattr(entry, "link", None),
                    published=pub,
                    provenance=Provenance(provider=self.name, source_kind=SourceKind.OFFICIAL,
                                          uri=getattr(entry, "link", None),
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
