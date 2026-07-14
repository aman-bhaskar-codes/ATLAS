"""Ntfy provider — absorbs the Phase-1 Notifier."""

from __future__ import annotations

import httpx

from atlas.capabilities.notification.domain.models import Channel


class NtfyProvider:
    name = "ntfy"
    supports_actions = True

    def __init__(self, base_url: str = "https://ntfy.sh") -> None:
        self._base = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=10.0)

    async def initialize(self) -> None: ...
    
    async def health(self) -> bool:
        return True
        
    async def send(self, channel: Channel, title: str, body: str,
                   *, actions: tuple[tuple[str, str], ...] = ()) -> bool:
        headers = {"Title": title}
        
        if actions:
            action_strs = []
            for label, url in actions:
                action_strs.append(f"view, {label}, {url}, clear=true")
            headers["Actions"] = "; ".join(action_strs)
            
        r = await self._client.post(f"{self._base}/{channel.address}", content=body.encode("utf-8"), headers=headers)
        return r.status_code == 200

    async def shutdown(self) -> None:
        await self._client.aclose()
