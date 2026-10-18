"""HTTPX transport preserving origin/TLS while pinning public numeric sockets.

Only public HTTPX/HTTPCore transport, network-backend and stream APIs are used.
No IP URL rewrite, alternate Host header, environment proxy or TLS bypass.
"""

from __future__ import annotations

import asyncio
import ssl
from collections.abc import AsyncIterator, Iterable
from typing import Any

import httpcore
import httpx

from atlas.capabilities.browser.errors import UnsafeURLError
from atlas.capabilities.browser.security.egress import EgressPolicy


class PinnedNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, policy: EgressPolicy, backend: httpcore.AsyncNetworkBackend | None = None) -> None:
        self._policy = policy
        self._backend = backend or httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 - HTTPCore public protocol signature
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        authority = f"[{host}]" if ":" in host else host
        # Resolution and retries share the connect timeout, not one per answer.
        async with asyncio.timeout(timeout if timeout is not None else 10.0):
            addresses = await self._policy.resolve_addresses(f"http://{authority}:{port}/")
            failure: Exception | None = None
            for address in addresses:
                try:
                    return await self._backend.connect_tcp(
                        address,
                        port,
                        timeout=timeout,
                        local_address=local_address,
                        socket_options=socket_options,
                    )
                except (httpcore.ConnectError, httpcore.ConnectTimeout, OSError) as exc:
                    failure = exc
            assert failure is not None
            raise failure

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,  # noqa: ASYNC109 - HTTPCore public protocol signature
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise UnsafeURLError("egress denied: Unix socket transport is not permitted")

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)


class _ResponseStream(httpx.AsyncByteStream):
    def __init__(self, stream: Any) -> None:
        self._stream = stream

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for part in self._stream:
            yield part

    async def aclose(self) -> None:
        await self._stream.aclose()


class PinnedHTTPTransport(httpx.AsyncBaseTransport):
    """A public transport bridge, with hostname origin and default CA validation."""

    def __init__(self, policy: EgressPolicy, *, backend: httpcore.AsyncNetworkBackend | None = None) -> None:
        self._policy = policy
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl.create_default_context(),
            network_backend=PinnedNetworkBackend(policy, backend),
            max_connections=8,
            max_keepalive_connections=0,
            http2=False,
            retries=0,
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self._policy.check(str(request.url))
        core_request = httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions={**request.extensions, "sni_hostname": request.url.raw_host.decode("ascii")},
        )
        response = await self._pool.handle_async_request(core_request)
        return httpx.Response(
            status_code=response.status,
            headers=response.headers,
            stream=_ResponseStream(response.stream),
            extensions=response.extensions,
        )

    async def aclose(self) -> None:
        await self._pool.aclose()
