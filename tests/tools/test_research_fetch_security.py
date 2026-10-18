"""Bounded fetch regressions with controlled transports and DNS answers."""

from collections.abc import AsyncIterator

import httpx
import pytest

from atlas.capabilities.browser.errors import UnsafeURLError
from atlas.capabilities.browser.security.egress import EgressPolicy
from atlas.tools.research import HttpTextFetcher


async def public_dns(host: str) -> list[str]:
    return ["8.8.8.8"]


class OversizedStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.reads = 0
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for _ in range(100):
            self.reads += 1
            yield b"x" * 100_000

    async def aclose(self) -> None:
        self.closed = True


async def test_fetcher_stops_stream_at_download_limit_and_reports_failure() -> None:
    stream = OversizedStream()
    fetcher = HttpTextFetcher(
        egress_policy=EgressPolicy(resolver=public_dns),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream)),
    )
    with pytest.raises(ValueError, match="download limit"):
        await fetcher("https://public.example.test/")
    assert stream.reads <= 21
    assert stream.closed


async def test_fetcher_rejects_oversized_content_length_before_reading() -> None:
    stream = OversizedStream()
    fetcher = HttpTextFetcher(
        egress_policy=EgressPolicy(resolver=public_dns),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers={"content-length": "3000000"}, stream=stream)
        ),
    )
    with pytest.raises(ValueError, match="download limit"):
        await fetcher("https://public.example.test/")
    assert stream.reads == 0
    assert stream.closed


async def test_fetcher_denies_redirect_to_private_dns_without_requesting_it() -> None:
    requests: list[str] = []

    async def dns(host: str) -> list[str]:
        return ["10.0.0.1"] if host == "redirect.example.test" else ["8.8.8.8"]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://redirect.example.test/"})

    fetcher = HttpTextFetcher(egress_policy=EgressPolicy(resolver=dns), transport=httpx.MockTransport(handler))
    with pytest.raises(UnsafeURLError):
        await fetcher("https://public.example.test/")
    assert requests == ["https://public.example.test/"]


async def test_fetcher_follows_public_redirect_and_extracts_text() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/paper"})
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            stream=httpx.ByteStream(b"<head><title>Paper</title></head><p>Body</p>"),
        )

    fetcher = HttpTextFetcher(egress_policy=EgressPolicy(resolver=public_dns), transport=httpx.MockTransport(handler))
    title, text, content_type = await fetcher("https://public.example.test/start")
    assert title == "Paper" and "Body" in text and content_type == "text/markdown"
    assert requests == ["https://public.example.test/start", "https://public.example.test/paper"]


async def test_compressed_expansion_is_rejected_before_any_body_decode() -> None:
    stream = OversizedStream()
    fetcher = HttpTextFetcher(
        egress_policy=EgressPolicy(resolver=public_dns),
        transport=httpx.MockTransport(lambda request: httpx.Response(
            200, headers={"content-encoding": "gzip"}, stream=stream,
        )),
    )
    with pytest.raises(ValueError, match="compressed"):
        await fetcher("https://public.example.test/")
    assert stream.reads == 0 and stream.closed


async def test_total_redirect_deadline_does_not_reset_per_hop() -> None:
    import asyncio

    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.04)
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/final"})
        return httpx.Response(200, stream=httpx.ByteStream(b"Evidence"))

    fetcher = HttpTextFetcher(timeout_s=0.06, egress_policy=EgressPolicy(resolver=public_dns),
                              transport=httpx.MockTransport(handler))
    with pytest.raises(TimeoutError):
        await fetcher("https://public.example.test/start")
