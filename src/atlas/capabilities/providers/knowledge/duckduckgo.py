"""DuckDuckGo provider (Lite API) — keyless web corroboration.

Rate limited, uses HTML parsing on the Lite version. source_kind='web'.
"""

from __future__ import annotations

import re
import urllib.parse
from datetime import UTC, datetime
from typing import Any

import httpx

from atlas.capabilities.domain.common import Provenance, SourceKind
from atlas.capabilities.domain.knowledge import KnowledgeItem
from atlas.capabilities.providers.base import CapabilityRequest, RetryPolicy
from atlas.capabilities.registry.capability import Capability


class DuckDuckGoProvider:
    name = "duckduckgo"
    capability = Capability.KNOWLEDGE
    is_local = False
    requires_auth = False
    source_kind = "web"

    def __init__(self, timeout_s: float = 15.0) -> None:
        self._client = httpx.AsyncClient(timeout=timeout_s, follow_redirects=True, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        })

    async def initialize(self) -> None: ...
    async def authenticate(self) -> None: ...

    async def health(self) -> bool:
        try:
            r = await self._client.get("https://html.duckduckgo.com/html/")
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def search(self, query: str, *, limit: int) -> list[KnowledgeItem]:
        try:
            r = await self._client.post(
                "https://html.duckduckgo.com/html/",
                data={"q": query}
            )
            r.raise_for_status()
            
            # Very basic regex scraping for Lite HTML
            # <a class="result__url" href="...">...</a>
            # <a class="result__snippet" ...>...</a>
            
            html = r.text
            items: list[KnowledgeItem] = []
            
            # Simple chunking by result
            results = html.split('<div class="result__body links_main links_deep">')[1:]
            for res in results[:limit]:
                title_match = re.search(r'<a class="result__snippet[^>]*>([^<]+)</a>', res)
                url_match = re.search(r'<a class="result__url" href="([^"]+)"', res)
                
                if url_match:
                    url = urllib.parse.unquote(url_match.group(1))
                    if url.startswith("//duckduckgo.com/l/?uddg="):
                        url = urllib.parse.unquote(url.split("uddg=")[1].split("&")[0])
                        
                    title = title_match.group(1).strip() if title_match else "DDG Result"
                    items.append(KnowledgeItem(
                        title=title, snippet=title, # snippet is same as title in this basic scrape
                        url=url,
                        published=None,
                        provenance=Provenance(provider=self.name, source_kind=SourceKind.WEB,
                                              uri=url, retrieved_ts=datetime.now(UTC))))
            return items
        except (httpx.HTTPError, Exception):
            return []

    async def execute(self, request: CapabilityRequest) -> Any:
        return await self.search(str(request.args.get("query", "")),
                                 limit=int(request.args.get("limit", 6)))

    def normalize(self, raw: Any) -> Any:
        return raw

    def retry_policy(self) -> RetryPolicy:
        return RetryPolicy(max_attempts=2, base_backoff_s=2.0) # More backoff for rate limits

    async def shutdown(self) -> None:
        await self._client.aclose()
