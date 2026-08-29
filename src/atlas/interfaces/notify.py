"""Notifications + confirmations.

WHY push-first: an unattended agent must reach the user off the terminal. ntfy
is free and supports action buttons that POST back to a local callback. WHY the
callback carries a token: an unauthenticated POST must not be able to approve a
Tier-2 action. Timeout resolves to DENY (fail-closed).
"""

from __future__ import annotations

import asyncio
import secrets
import sys
from typing import Protocol

import httpx

from atlas.infra.errors import AtlasError
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
                "Title": title,
                "Priority": "5",
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


class ConfirmationRequiredError(AtlasError):
    """Raised by a confirmer when an action needs explicit human approval and
    there is no interactive channel available (HTTP request, headless process,
    non-TTY stdin). Carries everything the route layer needs to render a 4xx
    challenge the caller can later resolve and re-submit."""

    def __init__(
        self,
        prompt: str,
        decision: SafetyDecision,
        req: ToolRequest,
        approval_id: str | None = None,
    ) -> None:
        super().__init__(prompt)
        self.prompt = prompt
        self.decision = decision
        self.request = req
        self.approval_id = approval_id or f"apr_{secrets.token_urlsafe(12)}"
        self.tier = decision.tier
        self.matched_rule = decision.matched_rule
        self.reason = decision.reason


class CliConfirmer:
    """Dev-mode confirmer. WHY kept: fast local dev loop without a phone in hand.

    In an interactive TTY it prompts on stdin. In a non-interactive context
    (HTTP server, piped input, headless) it does NOT call input() — that would
    raise EOFError and 500 the request. Instead it raises
    ``ConfirmationRequiredError`` so the route layer can return a 4xx challenge
    the caller can resolve out-of-band and retry."""

    def __init__(self, *, allow_stdin: bool | None = None) -> None:
        # Auto-detect: True when stdin is a real TTY, False otherwise.
        # Tests / callers can override explicitly.
        if allow_stdin is None:
            try:
                allow_stdin = sys.stdin.isatty()
            except Exception:
                allow_stdin = False
        self._allow_stdin = allow_stdin

    async def confirm(self, prompt: str, decision: SafetyDecision, req: ToolRequest) -> bool:
        if self._allow_stdin:
            print(prompt)
            answer = await asyncio.to_thread(input, "approve? [y/N] ")
            return answer.strip().lower() in {"y", "yes"}
        _log.warning(
            "notify.confirm_required_no_tty",
            event_type="notify",
            correlation_id=req.correlation_id,
            tool=req.tool,
            operation=req.operation,
            tier=str(decision.tier.name),
            detail="non-interactive context; raising ConfirmationRequiredError",
        )
        raise ConfirmationRequiredError(prompt, decision, req)


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
                _log.info("notify.confirm_timeout", event_type="notify", correlation_id=req.correlation_id)
                return False  # fail-closed
            return result
        return await self._cli.confirm(prompt, decision, req)
