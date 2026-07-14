"""SessionManager coordinates acquisition and profile injection."""
from __future__ import annotations

import uuid
from typing import Any

from atlas.capabilities.browser.domain.session import BrowserSession, ContextSpec
from atlas.capabilities.browser.session.pool import BrowserPool
from atlas.infra.ids import IdGenerator


class SessionManager:
    def __init__(self, pool: BrowserPool, ids: IdGenerator, sandbox: Any = None) -> None:
        self._pool = pool
        self._ids = ids
        self._sandbox = sandbox

    async def acquire(self, *, profile: str | None = None, incognito: bool = False) -> BrowserSession:
        spec = ContextSpec(profile_name=profile, incognito=incognito)
        # Use an execution ID for tracing, or a newly generated UUID for this session instance.
        session_id = f"sess_{uuid.uuid4().hex[:8]}"
        return await self._pool.acquire(session_id, spec, sandbox_spec=self._sandbox)

    async def release(self, session_id: str) -> None:
        await self._pool.release(session_id)
