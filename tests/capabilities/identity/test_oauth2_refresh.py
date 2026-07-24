from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from atlas.capabilities.identity.auth.oauth2 import OAuth2Strategy
from atlas.capabilities.identity.models import Credential, CredentialKind, Token


@pytest.mark.asyncio
async def test_expired_token_refreshes_and_rotates(monkeypatch: pytest.MonkeyPatch) -> None:
    strategy = OAuth2Strategy(token_url="http://fake.token/url", client_id="a", client_secret="b")
    
    expired_token = Token(
        access_token="old_access",
        refresh_token="old_refresh",
        expires_at=datetime.now(UTC) - timedelta(hours=1),
        scopes=("read",)
    )
    credential = Credential(
        id="c1",
        kind=CredentialKind.OAUTH2,
        provider_hint="google",
        secret=expired_token.model_dump_json(),
        expires_at=expired_token.expires_at,
        scopes=("read",),
        rotated_ts=None,
    )
    
    assert not await strategy.valid(credential)
    
    # Mock httpx.AsyncClient.post
    class FakeResponse:
        def raise_for_status(self) -> None:
            pass
        def json(self) -> dict[str, str | int]:
            return {
                "access_token": "new_access",
                "refresh_token": "new_refresh",
                "expires_in": 3600
            }
            
    async def mock_post(*args: Any, **kwargs: Any) -> FakeResponse:
        return FakeResponse()
        
    monkeypatch.setattr(strategy._client, "post", mock_post)
    
    new_cred = await strategy.refresh(credential)
    
    assert await strategy.valid(new_cred)
    assert strategy._token(new_cred).access_token == "new_access"
    
    tok = strategy._token(new_cred)
    assert tok.refresh_token == "new_refresh"
    assert tok.access_token == "new_access"
    assert new_cred.rotated_ts is not None
    
    await strategy.close()
