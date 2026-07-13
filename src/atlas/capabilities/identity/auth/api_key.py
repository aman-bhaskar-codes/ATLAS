"""API Key strategy — static credential.

WHY: API keys (like Brave, Tavily) never expire and don't refresh. They are just
stored and retrieved.
"""

from __future__ import annotations

from atlas.capabilities.identity.models import Credential


class ApiKeyStrategy:
    async def valid(self, credential: Credential) -> bool:
        return True

    async def refresh(self, credential: Credential) -> Credential:
        return credential  # No-op for static API keys

    async def usable_secret(self, credential: Credential) -> str:
        return credential.secret
