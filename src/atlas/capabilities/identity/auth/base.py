"""Auth strategy protocol — provider-independent credential acquisition/refresh.

WHY a protocol keyed by CredentialKind: the Identity Platform picks a strategy by
the credential's kind, not by the provider. So OAuth2 works identically for Gmail,
Calendar, Drive, and GitHub — one strategy, many providers. Adding a new auth
mechanism (enterprise SSO later) is a new strategy, zero provider changes.
"""

from __future__ import annotations

from typing import Protocol

from atlas.capabilities.identity.models import Credential


class AuthStrategy(Protocol):
    async def valid(self, credential: Credential) -> bool: ...
    async def refresh(self, credential: Credential) -> Credential: ...
    async def usable_secret(self, credential: Credential) -> str: ...
    # the header/token value a provider actually sends
