"""ContactsProvider protocol — Google People now, CardDAV later."""
from __future__ import annotations

from typing import Protocol

from atlas.capabilities.domain.contacts import Contact, ContactDraft


class ContactsProvider(Protocol):
    name: str
    requires_auth: bool

    async def initialize(self) -> None: ...
    async def authenticate(self) -> None: ...
    async def health(self) -> bool: ...

    async def search(self, query: str, *, limit: int) -> list[Contact]: ...   # Tier-1
    async def get(self, contact_id: str) -> Contact: ...                       # Tier-1
    async def list_all(self, *, limit: int) -> list[Contact]: ...              # for known-set sync
    async def create(self, draft: ContactDraft) -> str: ...                    # Tier-2
    async def update(self, draft: ContactDraft) -> str: ...                    # Tier-2

    async def shutdown(self) -> None: ...
