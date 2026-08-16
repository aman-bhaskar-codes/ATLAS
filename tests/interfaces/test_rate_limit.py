"""Batch 8 tests — token-bucket rate limiting and the 429 path."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from atlas.interfaces.api.rate_limit import TokenBucketLimiter, rate_limit


class TestTokenBucket:
    def test_burst_then_throttle(self) -> None:
        limiter = TokenBucketLimiter(capacity=3, refill_per_minute=0)  # no refill
        results = [limiter.allow("k")[0] for _ in range(5)]
        assert results == [True, True, True, False, False]

    def test_retry_after_positive(self) -> None:
        limiter = TokenBucketLimiter(capacity=1, refill_per_minute=60)
        assert limiter.allow("k")[0] is True
        allowed, retry_after = limiter.allow("k")
        assert allowed is False and retry_after > 0

    def test_refill_over_time(self) -> None:
        import time

        limiter = TokenBucketLimiter(capacity=1, refill_per_minute=6000)  # 100/s
        limiter.allow("k")
        time.sleep(0.03)  # ~3 tokens refilled
        allowed, _ = limiter.allow("k")
        assert allowed is True

    def test_independent_keys(self) -> None:
        limiter = TokenBucketLimiter(capacity=1, refill_per_minute=0)
        assert limiter.allow("a")[0] is True
        assert limiter.allow("b")[0] is True
        assert limiter.allow("a")[0] is False

    def test_prune(self) -> None:
        limiter = TokenBucketLimiter()
        limiter.allow("k")
        limiter.prune(max_age_s=0.0)
        assert limiter._buckets == {}


class TestMiddleware:
    def test_429_with_retry_after(self) -> None:
        app = FastAPI()
        app.state.rate_limiter = TokenBucketLimiter(capacity=1, refill_per_minute=0)

        @app.middleware("http")
        async def _rl(request, call_next):  # type: ignore[no-untyped-def]
            throttled = await rate_limit(request)
            if throttled is not None:
                return throttled
            return await call_next(request)

        @app.get("/ping")
        async def ping() -> dict[str, str]:
            return {"pong": "yes"}

        client = TestClient(app)
        assert client.get("/ping").status_code == 200
        resp = client.get("/ping")
        assert resp.status_code == 429
        assert "Retry-After" in resp.headers

    def test_no_limiter_configured_is_open(self) -> None:
        app = FastAPI()

        @app.middleware("http")
        async def _rl(request, call_next):  # type: ignore[no-untyped-def]
            throttled = await rate_limit(request)
            if throttled is not None:
                return throttled
            return await call_next(request)

        @app.get("/ping")
        async def ping() -> dict[str, str]:
            return {"pong": "yes"}

        client = TestClient(app)
        for _ in range(10):
            assert client.get("/ping").status_code == 200
