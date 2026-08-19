"""BrowserTool — exposes the BrowserPlatform to the LLM agent via the Tool protocol."""

from __future__ import annotations

from typing import Any

from atlas.capabilities.browser.domain.locator import Locator, LocatorKind
from atlas.capabilities.browser.domain.page import PageHandle
from atlas.capabilities.browser.platform import BrowserPlatform
from atlas.infra.ids import CorrelationId
from atlas.infra.logging import get_logger
from atlas.infra.types import ToolResult

_log = get_logger("atlas.tools.browser")


class BrowserTool:
    name = "browser"

    def __init__(
        self,
        platform: BrowserPlatform,
        ids: Any,  # Used to generate CorrelationIds
    ) -> None:
        self._platform = platform
        self._ids = ids
        self._default_session_id: str | None = None
        self._default_handle: PageHandle | None = None

    async def _ensure_handle(self) -> PageHandle:
        """Ensure a default browser session and tab exist."""
        if not self._default_session_id:
            session = await self._platform.create_session()
            self._default_session_id = session.id
        if not self._default_handle:
            self._default_handle = await self._platform.new_page(self._default_session_id)
        return self._default_handle

    def dry_run(self, args: dict[str, Any]) -> str:
        op = str(args.get("operation", ""))
        url = str(args.get("url", args.get("seed_url", "")))
        
        if op == "research":
            return f"CRAWL {url} (depth={args.get('depth', 1)}, budget={args.get('budget', 5)})"
        if op == "goto":
            return f"NAVIGATE to {url}"
        if op == "extract":
            return "EXTRACT article from current page"
        if op == "click":
            selector = str(args.get("selector", ""))
            return f"CLICK element matching {selector}"
        
        return f"unknown browser op {op!r}"

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        op = str(args.get("operation", ""))
        cid = CorrelationId(self._ids.generate("cid"))

        try:
            if op == "research":
                seed = str(args["seed_url"])
                depth = int(args.get("depth", 1))
                budget = int(args.get("budget", 5))
                
                # Auto-create session if needed for crawler
                if not self._default_session_id:
                    session = await self._platform.create_session()
                    self._default_session_id = session.id
                    
                result = await self._platform.research(self._default_session_id, seed, depth, budget, cid)
                
                # Serialize the result
                output = {
                    "seed_url": result.seed_url,
                    "articles_extracted": len(result.articles),
                    "visited_urls": list(result.visited_urls),
                    "confidence": result.confidence,
                    "articles": [
                        {
                            "title": a.title,
                            "markdown_length": len(a.markdown),
                            "preview": a.markdown[:500] + "..." if len(a.markdown) > 500 else a.markdown
                        }
                        for a in result.articles
                    ]
                }
                return ToolResult(ok=True, output=output)

            elif op == "goto":
                url = str(args["url"])
                handle = await self._ensure_handle()
                await self._platform.goto(handle, url, cid)
                return ToolResult(ok=True, output={"status": "navigated", "url": url})

            elif op == "extract":
                handle = await self._ensure_handle()
                article = await self._platform.extract_article(handle, cid)
                return ToolResult(ok=True, output={"title": article.title, "markdown": article.markdown})

            elif op == "click":
                handle = await self._ensure_handle()
                selector = str(args["selector"])
                # Simplistic locator parsing for the LLM
                locator = Locator(kind=LocatorKind.CSS, value=selector)
                await self._platform.click(handle, locator, cid)
                return ToolResult(ok=True, output={"status": "clicked", "selector": selector})

            return ToolResult(ok=False, error=f"unknown operation {op!r}")

        except KeyError as exc:
            return ToolResult(ok=False, error=f"missing argument {exc}")
        except Exception as exc:
            _log.exception("Browser tool execution failed")
            return ToolResult(ok=False, error=str(exc))
