"""JWT strategy — service accounts.

WHY: For server-to-server auth (like Google Cloud Service Accounts), the stored
secret is the private key. "Refresh" here means minting a new JWT locally using
that key.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from atlas.capabilities.identity.models import Credential


class JwtStrategy:
    async def valid(self, credential: Credential) -> bool:
        if credential.expires_at is None:
            return False
        return credential.expires_at > datetime.now(UTC) + timedelta(seconds=60)

    async def refresh(self, credential: Credential) -> Credential:
        # NOTE: A real implementation would parse credential.secret (e.g. JSON key file),
        # generate a new JWT, and sign it. Since we only use OAuth2 for now, this is
        # a placeholder that we will flesh out when we integrate a JWT-based API.
        new_expires = datetime.now(UTC) + timedelta(hours=1)
        return credential.model_copy(update={
            "expires_at": new_expires,
            "rotated_ts": datetime.now(UTC)
        })

    async def usable_secret(self, credential: Credential) -> str:
        # In a real implementation, this would return the generated JWT string.
        # We assume the credential.secret contains the config/key needed to mint it,
        # but the usable_secret is the minted JWT itself.
        # For this stub, we just return the raw secret until implemented.
        return credential.secret
