"""Real HTTP core driven by an in-memory TCP peer: no network probes."""

from __future__ import annotations

import ssl
from collections.abc import Iterable
from typing import Any

import httpcore
import pytest

from atlas.capabilities.browser.errors import UnsafeURLError
from atlas.capabilities.browser.security.egress import EgressPolicy
from atlas.tools.research import HttpTextFetcher


class FixtureStream(httpcore.AsyncNetworkStream):
    def __init__(self, response: bytes) -> None:
        self.response = response
        self.written = bytearray()
        self.tls_host: str | None = None
        self.verify_mode: ssl.VerifyMode | None = None
        self.closed = False

    async def read(
        self,
        max_bytes: int,
        timeout: float | None = None,  # noqa: ASYNC109 - HTTPCore public protocol signature
    ) -> bytes:
        result, self.response = self.response[:max_bytes], self.response[max_bytes:]
        return result

    async def write(
        self,
        buffer: bytes,
        timeout: float | None = None,  # noqa: ASYNC109 - HTTPCore public protocol signature
    ) -> None:
        self.written.extend(buffer)

    async def aclose(self) -> None:
        self.closed = True

    async def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,  # noqa: ASYNC109 - HTTPCore public protocol signature
    ) -> FixtureStream:
        self.tls_host = server_hostname
        self.verify_mode = ssl_context.verify_mode
        return self

    def get_extra_info(self, info: str) -> Any:
        return None


class FixtureNetwork(httpcore.AsyncNetworkBackend):
    def __init__(self, responses: list[bytes] | None = None) -> None:
        self.responses = responses or [
            b"HTTP/1.1 200 OK\r\nContent-Length: 8\r\nContent-Type: text/plain\r\n\r\nEvidence"
        ]
        self.connections: list[tuple[str, int]] = []
        self.streams: list[FixtureStream] = []

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 - HTTPCore public protocol signature
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> FixtureStream:
        self.connections.append((host, port))
        stream = FixtureStream(self.responses.pop(0))
        self.streams.append(stream)
        return stream

    async def sleep(self, seconds: float) -> None:
        pass


async def test_connection_uses_once_validated_ip_and_preserves_https_identity() -> None:
    from atlas.capabilities.browser.security.pinned_transport import PinnedHTTPTransport

    resolutions: list[str] = []

    async def rebinding_dns(host: str) -> list[str]:
        resolutions.append(host)
        return ["8.8.8.8"] if len(resolutions) == 1 else ["127.0.0.1"]

    network = FixtureNetwork()
    policy = EgressPolicy(resolver=rebinding_dns)
    fetcher = HttpTextFetcher(egress_policy=policy, transport=PinnedHTTPTransport(policy, backend=network))
    _, text, _ = await fetcher("https://public.test/report")
    assert text == "Evidence"
    assert resolutions == ["public.test"]
    assert network.connections == [("8.8.8.8", 443)]
    assert network.streams[0].tls_host == "public.test"
    assert network.streams[0].verify_mode == ssl.CERT_REQUIRED
    assert b"Host: public.test\r\n" in network.streams[0].written
    assert network.streams[0].closed


@pytest.mark.parametrize("answers", [["8.8.8.8", "10.0.0.1"], ["127.0.0.1"], [], ["invalid"]])
async def test_pinned_backend_never_connects_when_any_answer_is_not_public(answers: list[str]) -> None:
    from atlas.capabilities.browser.security.pinned_transport import PinnedHTTPTransport

    async def dns(host: str) -> list[str]:
        return answers

    network = FixtureNetwork()
    policy = EgressPolicy(resolver=dns)
    with pytest.raises(UnsafeURLError):
        await HttpTextFetcher(egress_policy=policy, transport=PinnedHTTPTransport(policy, backend=network))(
            "https://public.test/report"
        )
    assert network.connections == []


async def test_redirect_each_connection_is_pinned_and_private_hop_is_not_opened() -> None:
    from atlas.capabilities.browser.security.pinned_transport import PinnedHTTPTransport

    resolutions: list[str] = []

    async def dns(host: str) -> list[str]:
        resolutions.append(host)
        return ["8.8.8.8"] if host == "public.test" else ["127.0.0.1"]

    network = FixtureNetwork([b"HTTP/1.1 302 Found\r\nContent-Length: 0\r\nLocation: https://rebound.test/\r\n\r\n"])
    policy = EgressPolicy(resolver=dns)
    with pytest.raises(UnsafeURLError):
        await HttpTextFetcher(egress_policy=policy, transport=PinnedHTTPTransport(policy, backend=network))(
            "https://public.test/start"
        )
    assert network.connections == [("8.8.8.8", 443)]
    assert resolutions == ["public.test", "rebound.test"]
    assert network.streams[0].closed


async def test_whole_fetch_deadline_bounds_slow_dns_even_with_custom_backend() -> None:
    import asyncio

    from atlas.capabilities.browser.security.pinned_transport import PinnedHTTPTransport

    async def slow_dns(host: str) -> list[str]:
        await asyncio.sleep(5)
        return ["8.8.8.8"]

    network = FixtureNetwork()
    policy = EgressPolicy(resolver=slow_dns)
    with pytest.raises(TimeoutError):
        await HttpTextFetcher(
            timeout_s=0.05, egress_policy=policy, transport=PinnedHTTPTransport(policy, backend=network)
        )("https://public.test/")
    assert network.connections == []
