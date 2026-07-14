"""EmailProvider protocol — one contract for Gmail, IMAP/SMTP, Outlook (later).

WHY split read vs send methods: read/search are Tier-1 and safe; send is the
Tier-2 irreversible action. The platform gates them differently, so the contract
names them separately. Providers hold only a credential id (6.2) — never a secret.
"""

from __future__ import annotations

from typing import Protocol

from atlas.capabilities.domain.email import EmailDraft, EmailMessage, Thread


class EmailProvider(Protocol):
    name: str
    requires_auth: bool

    async def initialize(self) -> None: ...
    async def authenticate(self) -> None: ...
    async def health(self) -> bool: ...
    async def search(self, query: str, *, limit: int) -> list[EmailMessage]: ...
    async def get_thread(self, thread_id: str) -> Thread: ...
    async def send(self, draft: EmailDraft) -> str: ...     # returns provider message id
    async def shutdown(self) -> None: ...
