"""IMAP/SMTP provider — Standard email via IMAP (read) and SMTP (send).

Uses app passwords from the identity platform.
"""

from __future__ import annotations

from atlas.capabilities.domain.email import EmailDraft, EmailMessage, Thread
from atlas.capabilities.identity.platform import IdentityPlatform


class ImapSmtpProvider:
    name = "imap_smtp"
    requires_auth = True

    def __init__(self, identity: IdentityPlatform, credential_id: str, timeout_s: float = 30.0) -> None:
        self._identity = identity
        self._credential_id = credential_id

    async def initialize(self) -> None: ...
    async def authenticate(self) -> None: ...
    async def health(self) -> bool: return True
    async def search(self, query: str, *, limit: int) -> list[EmailMessage]: return []
    async def get_thread(self, thread_id: str) -> Thread: raise NotImplementedError
    async def send(self, draft: EmailDraft) -> str: raise NotImplementedError
    async def shutdown(self) -> None: ...
