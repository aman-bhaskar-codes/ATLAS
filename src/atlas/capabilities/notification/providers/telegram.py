"""Telegram provider."""

from __future__ import annotations

from typing import Any

import httpx

from atlas.capabilities.identity.models import CredentialKind
from atlas.capabilities.identity.platform import IdentityPlatform
from atlas.capabilities.notification.domain.models import Channel


class TelegramProvider:
    name = "telegram"
    supports_actions = True

    def __init__(self, identity: IdentityPlatform) -> None:
        self._identity = identity
        self._client = httpx.AsyncClient(timeout=10.0)

    async def initialize(self) -> None: ...
    
    async def health(self) -> bool:
        return True
        
    async def send(self, channel: Channel, title: str, body: str,
                   *, actions: tuple[tuple[str, str], ...] = ()) -> bool:
        # Try resolving via 6.2 vault
        token = ""
        if hasattr(self._identity, "resolve_secret"):
            token = await self._identity.resolve_secret("telegram_bot", CredentialKind.API_KEY)
        elif hasattr(self._identity, "get_secret"):
            token = await self._identity.get_secret("telegram_bot", CredentialKind.API_KEY)
        
        msg = f"*{title}*\n\n{body}"
        payload: dict[str, Any] = {"chat_id": channel.address, "text": msg, "parse_mode": "Markdown"}
        
        if actions:
            keyboard = [[{"text": label, "url": url}] for label, url in actions]
            payload["reply_markup"] = {"inline_keyboard": keyboard}
            
        r = await self._client.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload)
        return r.status_code == 200

    async def shutdown(self) -> None:
        await self._client.aclose()
