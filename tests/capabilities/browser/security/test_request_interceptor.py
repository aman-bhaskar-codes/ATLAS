"""Chromium egress: pause every target before execution and every request before IO."""

import asyncio
import json
from collections.abc import Callable
from typing import Any

import pytest

from atlas.capabilities.browser.security.egress import EgressPolicy
from atlas.capabilities.browser.security.request_interceptor import ChromiumEgressInterceptor


class CDPTransport:
    """Controlled protocol peer: nested Target sessions have real command replies."""

    def __init__(self) -> None:
        self.handlers: dict[str, Callable[..., Any]] = {}
        self.commands: list[tuple[str, str, dict[str, Any]]] = []
        self.detached = False
        self.target_sessions: dict[str, str] = {}
        self.target_infos: dict[str, dict[str, str]] = {}

    def on(self, event: str, handler: Callable[..., Any]) -> None:
        self.handlers[event] = handler

    def remove_listener(self, event: str, handler: Callable[..., Any]) -> None:
        self.handlers.pop(event, None)

    async def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        if method == "Target.attachToTarget":
            target = params["targetId"]
            self.handlers["Target.attachedToTarget"](
                {
                    "sessionId": self.target_sessions[target],
                    "targetInfo": self.target_infos[target],
                    "waitingForDebugger": False,
                }
            )
        if method == "Target.sendMessageToTarget":
            self._command(params["sessionId"], json.loads(params["message"]))
        else:
            self.commands.append(("browser", method, params))
        return {}

    def _command(self, session: str, command: dict[str, Any]) -> None:
        self.commands.append((session, command["method"], command.get("params", {})))
        if command["method"] == "Target.sendMessageToTarget":
            nested = command["params"]
            self.event(nested["sessionId"], "reply", {})  # unused method must never permit requests
            self.event(
                session,
                "Target.receivedMessageFromTarget",
                {
                    "sessionId": nested["sessionId"],
                    "message": json.dumps({"id": json.loads(nested["message"])["id"], "result": {}}),
                },
            )
            self.commands.append(
                (
                    nested["sessionId"],
                    json.loads(nested["message"])["method"],
                    json.loads(nested["message"]).get("params", {}),
                )
            )
        self.handlers["Target.receivedMessageFromTarget"](
            {"sessionId": session, "message": json.dumps({"id": command["id"], "result": {}})}
        )

    def event(self, session: str, method: str, params: dict[str, Any]) -> None:
        self.handlers["Target.receivedMessageFromTarget"](
            {"sessionId": session, "message": json.dumps({"method": method, "params": params})}
        )

    def attach(self, session: str, target: str, *, parent: str = "", opener: str = "") -> None:
        self.target_sessions[target] = session
        self.target_infos[target] = {"targetId": target, "type": "page", "openerId": opener}
        params = {"sessionId": session, "targetInfo": {"targetId": target, "type": "page", "openerId": opener}}
        if parent:
            self.event(parent, "Target.attachedToTarget", params)
        else:
            params["sessionId"] = "flat-" + session
            params["waitingForDebugger"] = False  # existing targets also use the root flat protocol
            self.handlers["Target.attachedToTarget"](params)

    async def detach(self) -> None:
        self.detached = True


class Browser:
    def __init__(self, transport: CDPTransport) -> None:
        self.transport = transport

    async def new_browser_cdp_session(self) -> CDPTransport:
        return self.transport


async def public_dns(host: str) -> list[str]:
    return ["8.8.8.8"]


async def settle() -> None:
    for _ in range(20):
        await asyncio.sleep(0)


async def test_each_redirect_and_subresource_is_denied_before_continue() -> None:
    transport = CDPTransport()
    guard = ChromiumEgressInterceptor(EgressPolicy(resolver=public_dns))
    await guard.start(Browser(transport))
    transport.attach("page-session", "page-target")
    await settle()
    for request_id, url in [
        ("first", "https://public.example.test/start"),
        ("redirect", "http://127.1/secret"),
        ("image", "http://169.254.169.254/latest/meta-data/"),
    ]:
        transport.event(
            "page-session",
            "Fetch.requestPaused",
            {
                "requestId": request_id,
                "request": {"url": url},
                "redirectedRequestId": "first",
            },
        )
    await settle()
    continued = [p["requestId"] for _, m, p in transport.commands if m == "Fetch.continueRequest"]
    denied = [p["requestId"] for _, m, p in transport.commands if m == "Fetch.failRequest"]
    assert continued == ["first"]
    assert sorted(denied) == ["image", "redirect"]
    await guard.close()
    assert transport.detached


async def test_nested_frame_and_popup_inherit_scoped_page_policy() -> None:
    transport = CDPTransport()
    guard = ChromiumEgressInterceptor(EgressPolicy(resolver=public_dns))
    await guard.start(Browser(transport))
    transport.attach("page", "root")
    await settle()
    guard.set_target_policy("root", EgressPolicy(frozenset({"allowed.test"}), resolver=public_dns))
    transport.attach("frame", "frame-target", parent="page")
    transport.attach("popup", "popup-target", opener="root")
    await settle()
    transport.event(
        "popup",
        "Fetch.requestPaused",
        {
            "requestId": "popup-request",
            "request": {"url": "https://outside.test/"},
        },
    )
    transport.event(
        "page",
        "Target.receivedMessageFromTarget",
        {
            "sessionId": "frame",
            "message": json.dumps(
                {
                    "method": "Fetch.requestPaused",
                    "params": {
                        "requestId": "frame-request",
                        "request": {"url": "https://outside.test/"},
                    },
                }
            ),
        },
    )
    await settle()
    assert sorted(p["requestId"] for _, m, p in transport.commands if m == "Fetch.failRequest") == [
        "frame-request",
        "popup-request",
    ]
    for session in ("page", "frame", "popup"):
        methods = [m for sid, m, _ in transport.commands if sid == session]
        assert methods.index("Fetch.enable") < methods.index("Runtime.runIfWaitingForDebugger")
    await guard.close()


async def test_failed_interception_setup_closes_target_without_resuming() -> None:
    class Unsupported(CDPTransport):
        def _command(self, session: str, command: dict[str, Any]) -> None:
            if command["method"] == "Fetch.enable":
                self.handlers["Target.receivedMessageFromTarget"](
                    {
                        "sessionId": session,
                        "message": json.dumps({"id": command["id"], "error": {"message": "unsupported"}}),
                    }
                )
                return
            super()._command(session, command)

    transport = Unsupported()
    guard = ChromiumEgressInterceptor(EgressPolicy(resolver=public_dns))
    await guard.start(Browser(transport))
    transport.attach("page", "target")
    await settle()
    assert any(m == "Target.closeTarget" and p["targetId"] == "target" for _, m, p in transport.commands)
    assert not any(m == "Runtime.runIfWaitingForDebugger" for _, m, _ in transport.commands)
    with pytest.raises(Exception, match="interception"):
        guard.check_ready()
    await guard.close()


async def test_popup_keeps_scope_after_opener_closes() -> None:
    transport = CDPTransport()
    guard = ChromiumEgressInterceptor(EgressPolicy(resolver=public_dns))
    await guard.start(Browser(transport))
    transport.attach("page", "root")
    await settle()
    guard.set_target_policy("root", EgressPolicy(frozenset({"allowed.test"}), resolver=public_dns))
    transport.attach("popup", "popup-target", opener="root")
    await settle()
    transport.handlers["Target.detachedFromTarget"]({"sessionId": "page"})
    transport.event(
        "popup",
        "Fetch.requestPaused",
        {
            "requestId": "after-close",
            "request": {"url": "https://outside.test/"},
        },
    )
    await settle()
    assert [p["requestId"] for _, m, p in transport.commands if m == "Fetch.failRequest"] == ["after-close"]
    await guard.close()


async def test_close_cancels_dns_request_without_continuing_it() -> None:
    entered = asyncio.Event()

    async def blocked_dns(host: str) -> list[str]:
        entered.set()
        await asyncio.Event().wait()
        return []

    transport = CDPTransport()
    guard = ChromiumEgressInterceptor(EgressPolicy(resolver=blocked_dns))
    await guard.start(Browser(transport))
    transport.attach("page", "target")
    await settle()
    transport.event(
        "page",
        "Fetch.requestPaused",
        {
            "requestId": "pending",
            "request": {"url": "https://public.test/"},
        },
    )
    await entered.wait()
    await guard.close()
    assert not any(m == "Fetch.continueRequest" for _, m, _ in transport.commands)
    assert not guard._tasks and not guard._pending
