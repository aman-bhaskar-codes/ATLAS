"""Authenticated, bounded HTTP/CONNECT egress for owned browser contexts.

The upstream socket always receives an already validated numeric address. HTTPS
and WebSocket tunnels preserve native TLS, origin, redirects and cookies. This
is a transport boundary, not an OS network sandbox or TLS interception proxy.
"""

from __future__ import annotations

import asyncio
import base64
import hmac
import secrets
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

import h11

from atlas.capabilities.browser.errors import UnsafeURLError
from atlas.capabilities.browser.security.egress import EgressPolicy
from atlas.infra.tasks import spawn

Connector = Callable[[str, int], Awaitable[tuple[asyncio.StreamReader, Any]]]
MAX_HEADER_BYTES = 32_768
MAX_REQUEST_BYTES = 8 * 1024 * 1024


class PinnedEgressProxy:
    def __init__(
        self,
        policy: EgressPolicy,
        *,
        connector: Connector | None = None,
        max_connections: int = 32,
        max_stream_bytes: int = 32 * 1024 * 1024,
        lifetime_s: float = 120.0,
        idle_timeout_s: float = 15.0,
    ) -> None:
        self._policy = policy
        self._connector = connector or asyncio.open_connection
        self._max_connections = max_connections
        self._max_bytes = max_stream_bytes
        self._lifetime = lifetime_s
        self._idle = idle_timeout_s
        self.password = secrets.token_urlsafe(32)
        self._auth = b"Basic " + base64.b64encode(f"atlas:{self.password}".encode())
        self._server: asyncio.Server | None = None
        self._clients: set[asyncio.Task[Any]] = set()
        self._constraints: list[EgressPolicy] = []
        self._destinations: dict[asyncio.Task[Any], str] = {}

    @property
    def port(self) -> int:
        if self._server is None:
            raise RuntimeError("egress proxy is not running")
        return int(self._server.sockets[0].getsockname()[1])

    @property
    def settings(self) -> dict[str, str]:
        return {
            "server": f"http://127.0.0.1:{self.port}",
            "username": "atlas",
            "password": self.password,
            "bypass": "<-loopback>",
        }

    async def start(self) -> None:
        if self._server is None:
            self._server = await asyncio.start_server(self._accept, "127.0.0.1", 0, limit=MAX_HEADER_BYTES)

    async def set_constraints(self, policies: list[EgressPolicy]) -> None:
        """Intersect managed tab scopes; close tunnels no longer in that scope."""
        self._constraints = list(policies)
        revoked: list[asyncio.Task[Any]] = []
        for task, url in list(self._destinations.items()):
            try:
                for policy in self._constraints:
                    policy.check(url)
            except UnsafeURLError:
                task.cancel()
                revoked.append(task)
        if revoked:
            await asyncio.gather(*revoked, return_exceptions=True)

    def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if len(self._clients) >= self._max_connections:
            writer.close()
            return
        task = spawn(self._serve(reader, writer), name="browser.egress.proxy")
        self._clients.add(task)
        task.add_done_callback(self._clients.discard)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        upstream: Any = None
        established = False
        try:
            async with asyncio.timeout(self._lifetime):
                header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), self._idle)
                if len(header) > MAX_HEADER_BYTES:
                    raise ValueError("header limit exceeded")
                incoming = h11.Connection(h11.SERVER, max_incomplete_event_size=MAX_HEADER_BYTES)
                incoming.receive_data(header)
                request = incoming.next_event()
                if not isinstance(request, h11.Request):
                    raise ValueError("invalid proxy request")
                auth = [v for k, v in request.headers if k.lower() == b"proxy-authorization"]
                if len(auth) != 1 or not hmac.compare_digest(auth[0], self._auth):
                    writer.write(
                        b'HTTP/1.1 407 Proxy Authentication Required\r\nProxy-Authenticate: Basic realm="atlas"'
                        b"\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                    )
                    await writer.drain()
                    return
                method = request.method.decode("ascii")
                target = request.target.decode("ascii")
                tunnel = method == "CONNECT"
                if tunnel:
                    if "/" in target or "?" in target or "#" in target:
                        raise ValueError("invalid CONNECT authority")
                    url = f"https://{target}/"
                    parts = urlsplit(url)
                    if parts.port is None:
                        raise ValueError("CONNECT requires a port")
                    if any(k in (b"transfer-encoding", b"content-length") and v != b"0" for k, v in request.headers):
                        raise ValueError("CONNECT body is not supported")
                else:
                    parts = urlsplit(target)
                    if parts.scheme not in ("http", "ws") or not parts.netloc:
                        raise ValueError("absolute HTTP proxy target required")
                    url = "http:" + target[len(parts.scheme) + 1 :]
                for policy in self._constraints:
                    policy.check(url)
                addresses = await self._policy.resolve_addresses(url)
                task = asyncio.current_task()
                assert task is not None
                self._destinations[task] = url
                port = parts.port or (443 if tunnel else 80)
                last_error: Exception | None = None
                async with asyncio.timeout(10):
                    for address in addresses:
                        try:
                            upstream_reader, upstream = await self._connector(address, port)
                            break
                        except OSError as exc:
                            last_error = exc
                    else:
                        raise OSError("all validated addresses failed") from last_error
                if tunnel:
                    writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                    await writer.drain()
                else:
                    # Re-encode one correctly framed request. Explicit close
                    # forces each subsequent HTTP request through validation.
                    upgrade = any(k == b"upgrade" and v.lower() == b"websocket" for k, v in request.headers)
                    headers = [
                        (k, v)
                        for k, v in request.headers
                        if k not in (b"proxy-authorization", b"proxy-connection") and (upgrade or k != b"connection")
                    ]
                    if not upgrade:
                        headers.append((b"connection", b"close"))
                    outgoing = h11.Connection(h11.CLIENT)
                    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
                    upstream.write(
                        outgoing.send(h11.Request(method=request.method, target=path.encode(), headers=headers))
                    )
                    body_bytes = 0
                    while True:
                        event = incoming.next_event()
                        if event is h11.NEED_DATA:
                            data = await asyncio.wait_for(reader.read(16_384), self._idle)
                            incoming.receive_data(data)
                        elif isinstance(event, h11.Data):
                            body_bytes += len(event.data)
                            if body_bytes > MAX_REQUEST_BYTES:
                                raise ValueError("request body limit exceeded")
                            upstream.write(outgoing.send(event))
                            await upstream.drain()
                        elif isinstance(event, h11.EndOfMessage):
                            upstream.write(outgoing.send(event))
                            await upstream.drain()
                            break
                        else:
                            raise ValueError("unsupported proxy request framing")
                established = True
                await self._relay(reader, writer, upstream_reader, upstream)
        except UnsafeURLError:
            if not established:
                writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                await writer.drain()
        except (
            TimeoutError,
            ValueError,
            OSError,
            h11.ProtocolError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
        ):
            if not established and not writer.is_closing():
                writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                try:
                    await writer.drain()
                except OSError:
                    pass
        finally:
            task = asyncio.current_task()
            if task is not None:
                self._destinations.pop(task, None)
            if upstream is not None:
                upstream.close()
                await upstream.wait_closed()
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    async def _relay(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        upstream_reader: asyncio.StreamReader,
        upstream_writer: Any,
    ) -> None:
        used = 0

        async def pump(source: asyncio.StreamReader, target: Any) -> None:
            nonlocal used
            while data := await asyncio.wait_for(source.read(16_384), self._idle):
                used += len(data)
                if used > self._max_bytes:
                    raise ValueError("proxy stream byte budget exceeded")
                target.write(data)
                await asyncio.wait_for(target.drain(), self._idle)

        tasks = [asyncio.create_task(pump(reader, upstream_writer)), asyncio.create_task(pump(upstream_reader, writer))]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        tasks = list(self._clients)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._clients.clear()
