"""BrowserPool manages concurrent browser sessions and evicts idle ones."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from atlas.capabilities.browser.domain.page import AuthState
from atlas.capabilities.browser.domain.session import BrowserSession, ContextSpec, SessionState
from atlas.capabilities.browser.errors import SessionError
from atlas.capabilities.browser.providers.base import BrowserProvider
from atlas.capabilities.browser.registry.provider_registry import ProviderRegistry

_log = logging.getLogger("atlas.browser.pool")

class BrowserPool:
    def __init__(self, registry: ProviderRegistry, max_concurrent: int = 3, idle_ttl_s: int = 300) -> None:
        self._registry = registry
        self._max_concurrent = max_concurrent
        self._idle_ttl_s = idle_ttl_s
        self._sessions: dict[str, BrowserSession] = {}
        self._providers: dict[str, BrowserProvider] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, session_id: str, spec: ContextSpec, sandbox_spec: Any = None) -> BrowserSession:
        async with self._lock:
            if session_id in self._sessions:
                return self._sessions[session_id]

            if len(self._sessions) >= self._max_concurrent:
                raise SessionError("Max concurrent browser sessions reached")

            # Need to pick a provider. In reality this might require checking required capabilities.
            provider = self._registry.best_available()
            if not provider:
                raise SessionError("No browser providers available")

            # Launch provider inside sandbox
            provider_session_id = await provider.launch(
                profile=spec.profile_name,
                incognito=spec.incognito,
                sandbox_spec=sandbox_spec
            )

            state = SessionState(
                session_id=provider_session_id,
                provider_name=provider.name,
                auth_state=AuthState.ANONYMOUS
            )
            session = BrowserSession(id=session_id, spec=spec, state=state)
            self._sessions[session_id] = session
            self._providers[session_id] = provider
            _log.info(f"Launched browser session {session_id} using {provider.name}")
            return session

    def get_provider(self, session_id: str) -> BrowserProvider:
        if session_id not in self._providers:
            raise SessionError(f"Session {session_id} not in pool")
        return self._providers[session_id]

    async def release(self, session_id: str) -> None:
        async with self._lock:
            if session_id in self._sessions:
                provider = self._providers[session_id]
                provider_session_id = self._sessions[session_id].state.session_id
                try:
                    await provider.close(provider_session_id)
                except Exception as exc:
                    _log.warning(f"Error closing browser session {session_id}: {exc}")
                del self._sessions[session_id]
                del self._providers[session_id]
                _log.info(f"Released browser session {session_id}")
