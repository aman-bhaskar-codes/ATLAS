"""Chromium request-stage egress interception, including redirects and child targets.

Playwright route handlers see only the first request in a redirect chain. A
browser CDP session instead auto-attaches pages, out-of-process frames and
workers *before* execution, enables Fetch on each target, and recursively pauses
child targets. Chrome retains native URL, origin, cookie and redirect semantics.
The localhost CDP control connection is not a page network request. This protocol layer guards
HTTP request destinations. Managed providers separately pin sockets through their
context proxy and apply native WebRTC preferences in a fresh owned profile.
Neither layer is an OS network sandbox.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from atlas.capabilities.browser.errors import ProviderError
from atlas.capabilities.browser.security.egress import EgressPolicy
from atlas.infra.tasks import spawn

_log = logging.getLogger("atlas.browser.egress")
_AUTO_ATTACH = {
    "autoAttach": True,
    "waitForDebuggerOnStart": True,
    "flatten": False,
    "filter": [
        {"type": kind, "exclude": False} for kind in ("page", "iframe", "worker", "shared_worker", "service_worker")
    ]
    + [{"exclude": True}],
}


class ChromiumEgressInterceptor:
    """Guard Chromium targets without rewriting browser requests or responses."""

    def __init__(self, policy: EgressPolicy) -> None:
        self._policy = policy
        self._cdp: Any = None
        self._sequence = 0
        self._pending: dict[tuple[str, int], asyncio.Future[dict[str, Any]]] = {}
        self._parents: dict[str, str | None] = {}  # session -> parent session
        self._targets: dict[str, str] = {}  # session -> target
        self._target_parents: dict[str, str] = {}  # frame/popup -> page/parent target
        self._policies: dict[str, EgressPolicy] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self._failure = ""
        self._closed = False
        self._attaching: set[str] = set()
        self._flat_sessions: dict[str, str] = {}
        self._ready: dict[str, asyncio.Event] = {}

    async def start(self, browser: Any) -> None:
        try:
            self._cdp = await browser.new_browser_cdp_session()
            self._cdp.on("Target.attachedToTarget", self._attached)
            self._cdp.on("Target.receivedMessageFromTarget", self._received)
            self._cdp.on("Target.detachedFromTarget", self._detached)
            await self._cdp.send("Target.setAutoAttach", {**_AUTO_ATTACH, "flatten": True})
        except Exception as exc:
            self._failure = str(exc)
            await self.close()
            raise ProviderError("Chromium egress interception is unavailable") from exc

    def check_ready(self) -> None:
        if self._failure or self._closed or self._cdp is None:
            raise ProviderError(f"Chromium egress interception unavailable: {self._failure or 'closed'}")

    def set_target_policy(self, target_id: str, policy: EgressPolicy) -> None:
        self.check_ready()
        self._policies[target_id] = policy

    async def set_page_policy(self, context: Any, page: Any, policy: EgressPolicy) -> None:
        self.check_ready()
        session = await context.new_cdp_session(page)
        try:
            info = await session.send("Target.getTargetInfo")
            target = str(info["targetInfo"]["targetId"])
            self.set_target_policy(target, policy)
            ready = self._ready.setdefault(target, asyncio.Event())
            async with asyncio.timeout(15):
                await ready.wait()
            self.check_ready()
        finally:
            await session.detach()

    def _schedule(self, coro: Any) -> None:
        if self._closed:
            coro.close()
            return
        task = spawn(coro, name="browser.egress")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _attached(self, params: dict[str, Any], parent: str | None = None) -> None:
        session = str(params["sessionId"])
        info = params["targetInfo"]
        target = str(info["targetId"])
        self._ready.setdefault(target, asyncio.Event())
        if parent is None and target not in self._attaching:
            # Browser auto-attach requires the flat protocol, while Playwright's
            # public CDPSession API cannot address its child sessions. Keep the
            # target paused and obtain a second, non-flat session for commands.
            self._attaching.add(target)
            self._flat_sessions[target] = session
            self._schedule(self._attach_command_session(target))
            return
        self._parents[session] = parent
        self._targets[session] = target
        ancestor = self._targets.get(parent or "") or str(info.get("openerId", ""))
        if ancestor:
            self._target_parents[target] = ancestor
        self._schedule(self._initialize(session, target))

    async def _attach_command_session(self, target: str) -> None:
        try:
            await self._cdp.send("Target.attachToTarget", {"targetId": target, "flatten": False})
        except Exception as exc:
            self._failure = str(exc)
            await self._cdp.send("Target.closeTarget", {"targetId": target})

    async def _initialize(self, session: str, target: str) -> None:
        try:
            await self._send(session, "Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]})
            await self._send(session, "Network.enable", {})
            # WebSocket destinations are enforced by the context proxy. Native
            # handshakes preserve WebSocket semantics; no blanket WS denial.
            await self._send(session, "Network.setBlockedURLs", {"urls": ["file://*", "ftp://*"]})
            await self._send(session, "Target.setAutoAttach", _AUTO_ATTACH)
            flat_session = self._flat_sessions.pop(target, None)
            if flat_session:
                await self._cdp.send("Target.detachFromTarget", {"sessionId": flat_session})
            await self._send(session, "Runtime.runIfWaitingForDebugger", {})
            self._ready[target].set()
        except Exception as exc:
            if session not in self._targets or self._closed:
                return  # target/context was closed during initialization
            self._failure = str(exc)
            self._ready[target].set()
            _log.error("Unable to initialize egress interception: %s", exc)
            # A target that cannot be guarded must never execute.
            await self._cdp.send("Target.closeTarget", {"targetId": target})

    def _received(self, params: dict[str, Any]) -> None:
        session = str(params["sessionId"])
        message = json.loads(params["message"])
        if "id" in message:
            future = self._pending.get((session, int(message["id"])))
            if future is not None and not future.done():
                if "error" in message:
                    future.set_exception(ProviderError(str(message["error"])))
                else:
                    future.set_result(message.get("result", {}))
            return
        method = message.get("method")
        data = message.get("params", {})
        if method == "Target.attachedToTarget":
            self._attached(data, session)
        elif method == "Target.receivedMessageFromTarget":
            self._received(data)
        elif method == "Target.detachedFromTarget":
            self._detached(data)
        elif method == "Fetch.requestPaused":
            self._schedule(self._request(session, data))

    def _policy_for(self, session: str) -> EgressPolicy:
        target = self._targets.get(session, "")
        seen: set[str] = set()
        while target and target not in seen:
            seen.add(target)
            if target in self._policies:
                return self._policies[target]
            target = self._target_parents.get(target, "")
        return self._policy

    async def _request(self, session: str, event: dict[str, Any]) -> None:
        request_id = str(event["requestId"])
        try:
            self.check_ready()
            await self._policy_for(session).check_resolved(str(event["request"]["url"]))
        except Exception as exc:
            _log.info("Browser egress request denied: %s", exc)
            method, params = "Fetch.failRequest", {"requestId": request_id, "errorReason": "BlockedByClient"}
        else:
            method, params = "Fetch.continueRequest", {"requestId": request_id}
        try:
            await self._send(session, method, params)
        except Exception as exc:
            if session in self._targets and not self._closed:
                self._failure = str(exc)
                await self._cdp.send("Target.closeTarget", {"targetId": self._targets[session]})

    async def _send(self, session: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._sequence += 1
        command_id = self._sequence
        key = (session, command_id)
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[key] = future
        envelope = {"sessionId": session, "message": json.dumps({"id": command_id, "method": method, "params": params})}
        try:
            async with asyncio.timeout(10):
                parent = self._parents[session]
                if parent is None:
                    await self._cdp.send("Target.sendMessageToTarget", envelope)
                else:
                    await self._send(parent, "Target.sendMessageToTarget", envelope)
                return await future
        except TimeoutError as exc:
            raise ProviderError(f"egress interception timed out: {method} on {session}") from exc
        finally:
            self._pending.pop(key, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()  # also retrieve replies when transport send itself fails

    def _detached(self, params: dict[str, Any]) -> None:
        session = str(params["sessionId"])
        inherited = self._policy_for(session)
        target = self._targets.pop(session, "")
        if target:
            for child, parent in list(self._target_parents.items()):
                if parent == target:
                    self._policies.setdefault(child, inherited)
            self._ready.pop(target, None)
            self._attaching.discard(target)
            self._flat_sessions.pop(target, None)
        self._parents.pop(session, None)
        self._target_parents.pop(target, None)
        self._policies.pop(target, None)
        for (pending_session, _), future in list(self._pending.items()):
            if pending_session == session and not future.done():
                future.cancel()

    async def close(self) -> None:
        """Called after managed contexts are closed, before browser teardown."""
        self._closed = True
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()
        self._parents.clear()
        self._targets.clear()
        self._target_parents.clear()
        self._policies.clear()
        self._attaching.clear()
        self._flat_sessions.clear()
        self._ready.clear()
        if self._cdp is not None:
            for event, callback in (
                ("Target.attachedToTarget", self._attached),
                ("Target.receivedMessageFromTarget", self._received),
                ("Target.detachedFromTarget", self._detached),
            ):
                self._cdp.remove_listener(event, callback)
            await self._cdp.detach()
            self._cdp = None
