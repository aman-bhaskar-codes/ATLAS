"""Owned Chrome through pinned proxy; every peer is a controlled local fixture."""

from __future__ import annotations

import asyncio
import base64
import hashlib
from pathlib import Path

import pytest

from atlas.capabilities.browser.providers.playwright_provider import PlaywrightProvider
from atlas.capabilities.browser.security.egress import EgressPolicy
from atlas.capabilities.browser.security.network_proxy import PinnedEgressProxy

CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
pytestmark = pytest.mark.skipif(not CHROME.exists(), reason="installed Chrome fixture executable unavailable")


async def test_managed_native_redirect_subresource_and_websocket_keep_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connected: list[tuple[str, int]] = []
    paths: list[str] = []

    async def fixture(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            raw = await reader.readuntil(b"\r\n\r\n")
            lines = raw.decode().split("\r\n")
            path = lines[0].split()[1]
            paths.append(path)
            headers = dict(line.split(": ", 1) for line in lines[1:] if ": " in line)
            if path == "/socket":
                key = headers.get("sec-websocket-key", headers.get("Sec-WebSocket-Key", ""))
                accept = base64.b64encode(
                    hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
                )
                writer.write(
                    b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                    b"Sec-WebSocket-Accept: " + accept + b"\r\n\r\n\x81\x02OK"
                )
                await writer.drain()
                await reader.read(1024)
            elif path == "/start":
                writer.write(
                    b"HTTP/1.1 302 Found\r\nLocation: /final\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                )
                await writer.drain()
            else:
                body = (
                    b'<html><img src="/asset"><img src="http://blocked.test/nope"><p>Fixture</p></html>'
                    if path == "/final"
                    else b"asset"
                )
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nConnection: close\r\nContent-Length: "
                    + str(len(body)).encode()
                    + b"\r\n\r\n"
                    + body
                )
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    async def dns(host: str) -> list[str]:
        return ["10.0.0.1"] if host == "blocked.test" else ["8.8.8.8"]

    upstream = await asyncio.start_server(fixture, "127.0.0.1", 0)
    upstream_port = upstream.sockets[0].getsockname()[1]

    async def connector(address: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        assert address == "8.8.8.8", "unsafe address reached connector; no private service is contacted"
        connected.append((address, port))
        return await asyncio.open_connection("127.0.0.1", upstream_port)

    original = PinnedEgressProxy.__init__

    def proxy_init(self: PinnedEgressProxy, policy: EgressPolicy, **kwargs: object) -> None:
        original(self, policy, connector=connector)  # type: ignore[arg-type]

    monkeypatch.setattr(PinnedEgressProxy, "__init__", proxy_init)
    provider = PlaywrightProvider(EgressPolicy(resolver=dns), executable_path=str(CHROME))
    try:
        session = await provider.launch(profile=None, incognito=True, sandbox_spec=None)
        tab = await provider.new_tab(session)
        await provider.goto(session, tab, "http://public.test/start")
        page = provider._get_page(session, tab)
        await page.wait_for_load_state("load")
        assert page.url == "http://public.test/final"
        assert await page.evaluate("location.origin") == "http://public.test"
        assert "/asset" in paths and "/nope" not in paths
        result = await page.evaluate("""() => new Promise(resolve => {
            const socket = new WebSocket('ws://public.test/socket');
            socket.onmessage = e => { resolve(e.data); socket.close(); };
            socket.onerror = () => resolve('FAILED');
        })""")
        assert result == "OK"
        assert "/socket" in paths and connected

        # This UDP socket belongs to the test, not an existing private service.
        # A native WebRTC bypass would deliver STUN datagrams to this fixture.
        received: list[bytes] = []
        ready = asyncio.Event()

        class DatagramFixture(asyncio.DatagramProtocol):
            def datagram_received(self, data: bytes, address: tuple[str, int]) -> None:
                received.append(data)
                ready.set()

        datagrams, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            DatagramFixture, local_addr=("127.0.0.1", 0)
        )
        try:
            udp_port = datagrams.get_extra_info("sockname")[1]
            datagrams.sendto(b"fixture readiness", ("127.0.0.1", udp_port))
            async with asyncio.timeout(1):
                await ready.wait()
            received.clear()
            candidates = await page.evaluate(
                """async port => {
                const pc = new RTCPeerConnection({iceServers:[{urls:`stun:127.0.0.1:${port}`}]});
                const candidates = [];
                pc.onicecandidate = event => { if (event.candidate) candidates.push(event.candidate.candidate); };
                pc.createDataChannel('fixture');
                await pc.setLocalDescription(await pc.createOffer());
                await new Promise(resolve => setTimeout(resolve, 1200));
                pc.close();
                return candidates;
            }""",
                udp_port,
            )
            assert received == []
            assert candidates == []
        finally:
            datagrams.close()
    finally:
        await provider.stop()
        upstream.close()
        await upstream.wait_closed()
        assert not provider._proxies and provider._browser is None
