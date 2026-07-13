from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from atlas.capabilities.domain.common import SourceKind
from atlas.capabilities.identity.errors import IdentityError
from atlas.capabilities.providers.knowledge.brave import BraveSearchProvider


@pytest.mark.asyncio
async def test_brave_provider_auth_failure() -> None:
    identity_platform = AsyncMock()
    identity_platform.get_usable_secret.side_effect = Exception("No key")
    
    provider = BraveSearchProvider(identity_platform, credential_id="brave:test")
    
    with pytest.raises(IdentityError):
        await provider.search("test search", limit=5)


@pytest.mark.asyncio
async def test_brave_provider_success() -> None:
    identity_platform = AsyncMock()
    identity_platform.get_usable_secret.return_value = "mock_key"
    
    provider = BraveSearchProvider(identity_platform, credential_id="brave:test")
    
    with patch("httpx.AsyncClient.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "web": {
                "results": [
                    {
                        "title": "Brave Mock Title",
                        "description": "Brave snippet",
                        "url": "http://brave.mock"
                    }
                ]
            }
        }
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp
        
        items = await provider.search("test search", limit=5)
        
        assert len(items) == 1
        assert items[0].title == "Brave Mock Title"
        assert items[0].snippet == "Brave snippet"
        assert items[0].provenance.source_kind == SourceKind.WEB
        assert items[0].provenance.uri == "http://brave.mock"
