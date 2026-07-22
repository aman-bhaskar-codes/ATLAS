"""Notifications + confirmations.

WHY push-first: an unattended agent must reach the user off the terminal. ntfy
is free and supports action buttons that POST back to a local callback. WHY the
callback carries a token: an unauthenticated POST must not be able to approve a
Tier-2 action. Timeout resolves to DENY (fail-closed).
"""

from __future__ import annotations

import asyncio
from typing import Protocol

import httpx

from atlas.infra.ids import IdGenerator
from atlas.infra.logging import get_logger
from atlas.infra.types import SafetyDecision, ToolRequest

_log = get_logger("atlas.notify")


class Notifier(Protocol):
    async def notify(self, title: str, body: str, *, priority: int = 3) -> None: ...
    async def ask(self, title: str, body: str, *, timeout_s: float) -> bool | None: ...


class NtfyNotifier:
    def __init__(self, topic: str, callback_base: str, ids: IdGenerator) -> None:
        self._topic = topic
        self._cb = callback_base.rstrip("/")
        self._ids = ids
        self._pending: dict[str, asyncio.Future[bool]] = {}
        self._client = httpx.AsyncClient(timeout=10.0)

    async def notify(self, title: str, body: str, *, priority: int = 3) -> None:
        await self._client.post(
            f"https://ntfy.sh/{self._topic}",
            content=body.encode(),
            headers={"Title": title, "Priority": str(priority)},
        )

    async def ask(self, title: str, body: str, *, timeout_s: float) -> bool | None:
        req_id = self._ids.execution_id()
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[bool] = loop.create_future()
        self._pending[req_id] = fut
        approve = f"{self._cb}/confirm/{req_id}?d=1"
        deny = f"{self._cb}/confirm/{req_id}?d=0"
        await self._client.post(
            f"https://ntfy.sh/{self._topic}",
            content=body.encode(),
            headers={
                "Title": title, "Priority": "5",
                "Actions": f"http, Approve, {approve}; http, Deny, {deny}",
            },
        )
        try:
            return await asyncio.wait_for(fut, timeout_s)
        except TimeoutError:
            return None
        finally:
            self._pending.pop(req_id, None)

    def resolve(self, req_id: str, decision: bool) -> None:
        fut = self._pending.get(req_id)
        if fut is not None and not fut.done():
            fut.set_result(decision)

    async def close(self) -> None:
        await self._client.aclose()


class CliConfirmer:
    """Dev-mode confirmer: prompts on stdin. WHY kept: fast local dev loop
    without a phone in hand."""

    async def confirm(self, prompt: str, decision: SafetyDecision, req: ToolRequest) -> bool:
        print(prompt)
        answer = await asyncio.to_thread(input, "approve? [y/N] ")
        return answer.strip().lower() in {"y", "yes"}


class CompositeConfirmer:
    """Satisfies the safety `Confirmer` protocol by delegating to a push
    Notifier, falling back to CLI if push is not configured."""

    def __init__(self, notifier: Notifier | None, cli: CliConfirmer, timeout_s: float) -> None:
        self._notifier = notifier
        self._cli = cli
        self._timeout_s = timeout_s

    async def confirm(self, prompt: str, decision: SafetyDecision, req: ToolRequest) -> bool:
        if self._notifier is not None:
            result = await self._notifier.ask(
                f"ATLAS confirm: {req.tool}.{req.operation}", prompt, timeout_s=self._timeout_s
            )
            if result is None:
                _log.info("notify.confirm_timeout", event_type="notify",
                          correlation_id=req.correlation_id)
                return False  # fail-closed
            return result
        return await self._cli.confirm(prompt, decision, req)
