"""Knowledge provider base.

WHY specialize: every knowledge source maps (query -> KnowledgeItem[]). We keep
the generic Provider protocol (so the dispatcher/health/registry work unchanged)
and add a typed search() the KnowledgePlatform calls directly for the multi-source
fan-out. execute()/normalize() delegate to search() so the safety-gated path and
the fan-out path share one implementation.
"""

from __future__ import annotations

from typing import Any, Protocol

from atlas.capabilities.domain.knowledge import KnowledgeItem
from atlas.capabilities.providers.base import CapabilityRequest, RetryPolicy
from atlas.capabilities.registry.capability import Capability


class KnowledgeProvider(Protocol):
    name: str
    capability: Capability          # always Capability.KNOWLEDGE
    is_local: bool
    requires_auth: bool
    source_kind: str                # 'official' | 'web' | 'local' | 'model'

    async def initialize(self) -> None: ...
    async def authenticate(self) -> None: ...
    async def health(self) -> bool: ...
    async def search(self, query: str, *, limit: int) -> list[KnowledgeItem]: ...
    async def execute(self, request: CapabilityRequest) -> Any: ...
    def normalize(self, raw: Any) -> Any: ...
    def retry_policy(self) -> RetryPolicy: ...
    async def shutdown(self) -> None: ...
