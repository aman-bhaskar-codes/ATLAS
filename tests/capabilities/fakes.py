from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from atlas.capabilities.providers.base import CapabilityRequest, RetryPolicy
from atlas.capabilities.registry.capability import Capability


class FakePayload(BaseModel):
    value: str


class FakeProvider:
    capability = Capability.KNOWLEDGE
    is_local = True
    requires_auth = False

    def __init__(self, name="fake", fail_times=0) -> None:  # type: ignore
        self.name = name
        self._fail_times = fail_times
        self.calls = 0

    async def initialize(self) -> None: ...
    async def authenticate(self) -> None: ...
    async def health(self): return True  # type: ignore
    async def execute(self, request: CapabilityRequest) -> Any:
        self.calls += 1
        if self.calls <= self._fail_times:
            raise RuntimeError("transient")
        return {"raw": request.args.get("q", "")}
    def normalize(self, raw: Any) -> FakePayload:
        return FakePayload(value=str(raw["raw"]))
    def retry_policy(self) -> RetryPolicy:
        return RetryPolicy(max_attempts=3, base_backoff_s=0)
    async def shutdown(self) -> None: ...
