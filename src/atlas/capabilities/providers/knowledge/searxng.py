"""SearXNG JSON API — self-hosted, keyless metasearch. source_kind='web'.

WHY SearXNG rather than a paid search API: directive rule "no paid search
service by default". The instance URL is operator-supplied config; when unset
the provider is simply not registered, so nothing degrades.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from atlas.capabilities.domain.common import Provenance, SourceKind
from atlas.capabilities.domain.knowledge import KnowledgeItem
from atlas.capabilities.providers.base import CapabilityRequest, RetryPolicy
from atlas.capabilities.providers.knowledge.scholarly import clean_text, parse_date
from atlas.capabilities.registry.capability import Capability


class SearxngProvider:
    name = "searxng"
    capability = Capability.KNOWLEDGE
    is_local = False  # an instance may be local, but treat results as web-trust
    requires_auth = False
    source_kind = "web"

    def __init__(self, base_url: str, timeout_s: float = 15.0, *, engines: str = "") -> None:
        self._base = base_url.rstrip("/")
        self._engines = engines.strip()
        self._client = httpx.AsyncClient(timeout=timeout_s, follow_redirects=True)

    async def initialize(self) -> None: ...
    async def authenticate(self) -> None: ...

    async def health(self) -> bool:
        if not self._base:
            return False
        try:
            r = await self._client.get(f"{self._base}/healthz")
            if r.status_code == 200:
                return True
            r = await self._client.get(f"{self._base}/", params={"q": "test", "format": "json"})
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def search(self, query: str, *, limit: int) -> list[KnowledgeItem]:
        if not self._base:
            return []
        params: dict[str, str] = {"q": query, "format": "json", "safesearch": "0"}
        if self._engines:
            params["engines"] = self._engines
        try:
            r = await self._client.get(f"{self._base}/search", params=params)
            r.raise_for_status()
            payload = r.json()
        except Exception:
            return []

        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            return []

        items: list[KnowledgeItem] = []
        for hit in results[:limit]:
            if not isinstance(hit, dict):
                continue
            title = clean_text(hit.get("title"), limit=400)
            url = str(hit.get("url") or "")
            if not title or not url:
                continue
            items.append(
                KnowledgeItem(
                    title=title,
                    snippet=clean_text(hit.get("content")) or title,
                    url=url,
                    published=parse_date(hit.get("publishedDate")),
                    provenance=Provenance(
                        provider=self.name,
                        source_kind=SourceKind.WEB,
                        uri=url,
                        retrieved_ts=datetime.now(UTC),
                    ),
                    external_ids={"engine": str(hit.get("engine") or "")} if hit.get("engine") else {},
                )
            )
        return items

    async def execute(self, request: CapabilityRequest) -> Any:
        return await self.search(str(request.args.get("query", "")), limit=int(request.args.get("limit", 6)))

    def normalize(self, raw: Any) -> Any:
        return raw

    def retry_policy(self) -> RetryPolicy:
        return RetryPolicy(max_attempts=2, base_backoff_s=1.0)

    async def shutdown(self) -> None:
        await self._client.aclose()
