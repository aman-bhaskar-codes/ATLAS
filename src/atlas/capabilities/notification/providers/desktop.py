"""Desktop provider — macOS osascript."""

from __future__ import annotations

import asyncio
import shlex

from atlas.capabilities.notification.domain.models import Channel


class DesktopProvider:
    name = "desktop"
    supports_actions = False

    async def initialize(self) -> None: ...
    async def health(self) -> bool:
        return True
        
    async def send(self, channel: Channel, title: str, body: str,
                   *, actions: tuple[tuple[str, str], ...] = ()) -> bool:
        script = f'display notification {shlex.quote(body)} with title {shlex.quote(title)}'
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", script,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        await proc.communicate()
        return proc.returncode == 0

    async def shutdown(self) -> None: ...
