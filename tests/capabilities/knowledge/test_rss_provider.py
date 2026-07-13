from unittest.mock import MagicMock, patch

import pytest

from atlas.capabilities.domain.common import SourceKind
from atlas.capabilities.providers.knowledge.rss import RSSProvider


@pytest.mark.asyncio
async def test_rss_provider() -> None:
    provider = RSSProvider(name="rss:test", feeds=["http://mock.feed"])
    
    with patch("httpx.AsyncClient.get") as mock_get, patch("feedparser.parse") as mock_parse:
        mock_resp = MagicMock()
        mock_resp.text = "rss content"
        mock_get.return_value = mock_resp
        
        mock_entry = type("Entry", (), {
            "title": "Mock search query Title",
            "summary": "This is a mock search query summary.",
            "link": "http://mock.feed/1",
        })()
        
        mock_feed = type("Feed", (), {
            "entries": [mock_entry]
        })()
        
        mock_parse.return_value = mock_feed
        
        items = await provider.search("mock search query", limit=5)
        
        assert len(items) == 1
        assert items[0].title == "Mock search query Title"
        assert items[0].snippet == "This is a mock search query summary."
        assert items[0].provenance.source_kind == SourceKind.OFFICIAL
