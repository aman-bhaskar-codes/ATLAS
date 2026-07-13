"""OAuth2 strategy — authorization-code flow + refresh + rotation.

WHY refresh lives here (not in providers): token lifecycle is identical across
Google/GitHub/Microsoft. The strategy checks expiry, refreshes with the refresh
token, ROTATES (stores the new refresh token if the provider issues one), and
returns a fresh access token. Providers never see refresh logic — they ask for a
usable token and get one.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx

from atlas.capabilities.identity.errors import RefreshFailed
from atlas.capabilities.identity.models import Credential, Token


class OAuth2Strategy:
    def __init__(self, *, token_url: str, client_id: str, client_secret: str) -> None:
        self._token_url = token_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._client = httpx.AsyncClient(timeout=30.0)

    def _token(self, credential: Credential) -> Token:
        return Token(**json.loads(credential.secret))

    async def valid(self, credential: Credential) -> bool:
        tok = self._token(credential)
        if tok.expires_at is None:
            return True
        return tok.expires_at > datetime.now(UTC) + timedelta(seconds=60)

    async def refresh(self, credential: Credential) -> Credential:
        tok = self._token(credential)
        if not tok.refresh_token:
            raise RefreshFailed(f"{credential.id}: no refresh token")
        try:
            r = await self._client.post(self._token_url, data={
                "grant_type": "refresh_token", "refresh_token": tok.refresh_token,
                "client_id": self._client_id, "client_secret": self._client_secret})
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise RefreshFailed(f"{credential.id}: {exc}") from exc
        data = r.json()
        new = Token(
            access_token=data["access_token"],
            # rotation: keep the new refresh token if issued, else retain the old
            refresh_token=data.get("refresh_token", tok.refresh_token),
            expires_at=datetime.now(UTC) + timedelta(seconds=int(data.get("expires_in", 3600))),
            scopes=tok.scopes)
        return credential.model_copy(update={
            "secret": new.model_dump_json(),
            "expires_at": new.expires_at,
            "rotated_ts": datetime.now(UTC),
        })

    async def usable_secret(self, credential: Credential) -> str:
        return self._token(credential).access_token

    async def close(self) -> None:
        await self._client.aclose()
