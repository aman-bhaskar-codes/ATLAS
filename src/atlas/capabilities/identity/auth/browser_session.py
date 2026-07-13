"""Browser session strategy — cookies and local storage.

WHY: For browser capabilities, the credential is the Playwright state dict (cookies).
"Refresh" involves driving the browser to re-login, which is deferred to the
browser capability layer.
"""

from __future__ import annotations

from atlas.capabilities.identity.models import Credential


class BrowserSessionStrategy:
    async def valid(self, credential: Credential) -> bool:
        # Browser sessions are assumed valid until a navigation fails with 401/403.
        return True

    async def refresh(self, credential: Credential) -> Credential:
        # Browser session refresh is out-of-band (requires driving Playwright).
        return credential

    async def usable_secret(self, credential: Credential) -> str:
        return credential.secret
