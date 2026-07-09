"""Provider protocol — the vendor-agnostic (and transport-agnostic) seam.

WHY these 7 methods and no more: a provider adapter's ONLY job is to talk to one
backend and normalize its output. Routing, identity storage, and safety live
elsewhere. Because the protocol says nothing about HOW the backend is reached, an
MCP-server adapter (providers/mcp/base.py) and a direct-SDK adapter satisfy the
exact same interface — the capability layer cannot tell them apart. That is how we
get 'hundreds of MCP capabilities' without a new layer (ADR-017).

execute() takes a typed CapabilityRequest and returns a RAW provider result;
normalize() maps that raw result to a domain payload. Splitting them lets us test
normalization independently and lets retry re-run execute() without re-normalizing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel

from atlas.capabilities.registry.capability import Capability


class CapabilityRequest(BaseModel):
    """What the dispatcher hands a provider. operation + typed args; the provider
    knows how to fulfill it for its backend."""
    model_config = {"frozen": True}
    capability: Capability
    operation: str
    args: dict[str, Any] = {}


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 2
    base_backoff_s: float = 0.5
    max_backoff_s: float = 8.0


class Provider(Protocol):
    name: str
    capability: Capability
    is_local: bool
    requires_auth: bool

    async def initialize(self) -> None: ...
    async def authenticate(self) -> None: ...            # no-op if requires_auth is False
    async def health(self) -> bool: ...
    async def execute(self, request: CapabilityRequest) -> Any: ...   # RAW backend result
    def normalize(self, raw: Any) -> BaseModel: ...       # RAW -> domain payload
    def retry_policy(self) -> RetryPolicy: ...
    async def shutdown(self) -> None: ...
