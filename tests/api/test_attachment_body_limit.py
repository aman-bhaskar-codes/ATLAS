"""Reject oversized uploads before the multipart parser can spool them."""

from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from atlas.interfaces.api.body_limit import AttachmentBodyLimitMiddleware


async def test_chunked_upload_limit_is_enforced_before_handler() -> None:
    app = FastAPI()
    accepted: list[bytes] = []

    @app.post("/api/v1/attachments")
    async def upload(request: Request) -> dict[str, bool]:
        accepted.append(await request.body())
        return {"ok": True}

    app.add_middleware(AttachmentBodyLimitMiddleware, max_bytes=8)

    async def chunks():
        yield b"12345"
        yield b"67890"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/attachments", content=chunks(), headers={"Content-Length": "1"})
    assert response.status_code == 413
    assert accepted == []


async def test_small_upload_body_and_unrelated_route_are_preserved() -> None:
    app = FastAPI()

    @app.post("/{path:path}")
    async def echo(request: Request) -> dict[str, str]:
        return {"body": (await request.body()).decode()}

    app.add_middleware(AttachmentBodyLimitMiddleware, max_bytes=8)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        small = await client.post("/api/v1/attachments", content=b"12345678")
        unrelated = await client.post("/api/v1/other", content=b"1234567890")
    assert small.json() == {"body": "12345678"}
    assert unrelated.json() == {"body": "1234567890"}
