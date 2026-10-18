"""A pinned proxy with controlled peers; no external or private-service probes."""

from __future__ import annotations

import asyncio
import base64

import pytest

from atlas.capabilities.browser.security.egress import EgressPolicy


class UpstreamWriter:
    def __init__(self) -> None:
        self.data = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.data.extend(data)

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        pass


async def test_proxy_pins_connect_after_one_resolution_and_preserves_tunnel_bytes() -> None:
    from atlas.capabilities.browser.security.network_proxy import PinnedEgressProxy

    resolutions: list[str] = []
    connected: list[tuple[str, int]] = []
    upstream = UpstreamWriter()

    async def dns(host: str) -> list[str]:
        resolutions.append(host)
        return ["8.8.8.8"] if len(resolutions) == 1 else ["127.0.0.1"]

    async def connect(address: str, port: int) -> tuple[asyncio.StreamReader, UpstreamWriter]:
        connected.append((address, port))
        reader = asyncio.StreamReader()
        reader.feed_data(b"opaque TLS fixture")
        reader.feed_eof()
        return reader, upstream

    proxy = PinnedEgressProxy(EgressPolicy(resolver=dns), connector=connect)
    await proxy.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
        auth = base64.b64encode(f"atlas:{proxy.password}".encode()).decode()
        writer.write(
            (
                "CONNECT public.test:443 HTTP/1.1\r\nHost: public.test:443\r\n"
                f"Proxy-Authorization: Basic {auth}\r\n\r\n"
            ).encode()
        )
        await writer.drain()
        assert b"200 Connection Established" in await reader.readuntil(b"\r\n\r\n")
        assert await reader.read() == b"opaque TLS fixture"
        writer.close()
        await writer.wait_closed()
        assert resolutions == ["public.test"]
        assert connected == [("8.8.8.8", 443)]
    finally:
        await proxy.close()
    assert upstream.closed


@pytest.mark.parametrize("answers", [["127.0.0.1"], ["8.8.8.8", "10.0.0.1"], []])
async def test_proxy_never_opens_denied_connect_target(answers: list[str]) -> None:
    from atlas.capabilities.browser.security.network_proxy import PinnedEgressProxy

    async def dns(host: str) -> list[str]:
        return answers

    async def forbidden(address: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        pytest.fail("denied destination reached socket connector")

    proxy = PinnedEgressProxy(EgressPolicy(resolver=dns), connector=forbidden)
    await proxy.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
        auth = base64.b64encode(f"atlas:{proxy.password}".encode()).decode()
        writer.write(
            (
                "CONNECT public.test:443 HTTP/1.1\r\nHost: public.test:443\r\n"
                f"Proxy-Authorization: Basic {auth}\r\n\r\n"
            ).encode()
        )
        await writer.drain()
        assert b"403" in await reader.read()
        writer.close()
        await writer.wait_closed()
    finally:
        await proxy.close()


async def test_proxy_requires_per_context_credentials_before_dns() -> None:
    from atlas.capabilities.browser.security.network_proxy import PinnedEgressProxy

    async def forbidden_dns(host: str) -> list[str]:
        pytest.fail("unauthenticated proxy request reached DNS")

    proxy = PinnedEgressProxy(EgressPolicy(resolver=forbidden_dns))
    await proxy.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
        writer.write(b"CONNECT public.test:443 HTTP/1.1\r\nHost: public.test:443\r\n\r\n")
        await writer.drain()
        assert b"407" in await reader.read()
        writer.close()
        await writer.wait_closed()
    finally:
        await proxy.close()


async def test_proxy_timeout_closes_slow_client_and_releases_owned_task() -> None:
    from atlas.capabilities.browser.security.network_proxy import PinnedEgressProxy

    proxy = PinnedEgressProxy(EgressPolicy(), lifetime_s=0.03)
    await proxy.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
        assert b"502" in await asyncio.wait_for(reader.read(), 1)
        writer.close()
        await writer.wait_closed()
    finally:
        await proxy.close()
    assert not proxy._clients


async def test_proxy_stream_limit_closes_tunnel_without_buffering_entire_body() -> None:
    from atlas.capabilities.browser.security.network_proxy import PinnedEgressProxy

    upstream = UpstreamWriter()

    async def dns(host: str) -> list[str]:
        return ["8.8.8.8"]

    async def connect(address: str, port: int) -> tuple[asyncio.StreamReader, UpstreamWriter]:
        reader = asyncio.StreamReader()
        reader.feed_data(b"x" * 1000)
        return reader, upstream

    proxy = PinnedEgressProxy(EgressPolicy(resolver=dns), connector=connect, max_stream_bytes=100)
    await proxy.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
        auth = base64.b64encode(f"atlas:{proxy.password}".encode()).decode()
        writer.write(
            (
                "CONNECT public.test:443 HTTP/1.1\r\nHost: public.test:443\r\n"
                f"Proxy-Authorization: Basic {auth}\r\n\r\n"
            ).encode()
        )
        await writer.drain()
        assert b"200 Connection Established" in await reader.readuntil(b"\r\n\r\n")
        assert await asyncio.wait_for(reader.read(), 1) == b""
        writer.close()
        await writer.wait_closed()
    finally:
        await proxy.close()
    assert upstream.closed
