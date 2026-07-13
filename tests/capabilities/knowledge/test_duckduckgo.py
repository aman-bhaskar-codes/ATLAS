from unittest.mock import MagicMock, patch

import pytest

from atlas.capabilities.domain.common import SourceKind
from atlas.capabilities.providers.knowledge.duckduckgo import DuckDuckGoProvider


@pytest.mark.asyncio
async def test_duckduckgo_provider_success() -> None:
    provider = DuckDuckGoProvider()
    
    with patch("httpx.AsyncClient.post") as mock_post:
        mock_resp = MagicMock()
        # Mock simple HTML structure that lite HTML parsing can extract
        mock_resp.text = """
        <html><body>
            <div class="result__body links_main links_deep">
                <a class="result__url" href="http://ddg.mock">ddg.mock</a>
                <a class="result__snippet">DDG mock snippet</a>
            </div>
        </body></html>
        """
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp
        
        items = await provider.search("test search", limit=5)
        
        assert len(items) == 1
        assert "DDG mock snippet" in items[0].snippet
        assert items[0].provenance.source_kind == SourceKind.WEB
        assert items[0].provenance.uri == "http://ddg.mock"
