"""Auth + HMAC on the event ingestion surface (routes_events).

These routes mutate the live MessageBus, so:
  * /events/emit and /events/{id}/replay require an ADMIN key (readonly = 403,
    missing/invalid = 401) once ATLAS_API_KEYS is configured;
  * /webhooks/{source} verifies provider-native HMAC signatures over the RAW
    body when the source's secret is configured — a forged body never reaches
    the bus. Sources with no configured secret stay open (local/dev).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from atlas.interfaces.api import routes_events


class FakeBus:
    def __init__(self) -> None:
        self.published: list[tuple[str, Any]] = []

    async def publish(self, topic: str, event: Any) -> None:
        self.published.append((topic, event))


def _client(
    *,
    api_keys: dict[str, str] | None = None,
    github_webhook_secret: str = "",
    stripe_webhook_secret: str = "",
) -> tuple[TestClient, FakeBus]:
    app = FastAPI()
    app.include_router(routes_events.router, prefix="")
    bus = FakeBus()
    # routes_events uses a module-global _deps; set it for the test and restore is unnecessary
    routes_events._deps = SimpleNamespace(manager=None, db=None, bus=bus)  # type: ignore[assignment]
    app.state.api_keys = api_keys or {}
    app.state.atlas = SimpleNamespace(
        settings=SimpleNamespace(
            github_webhook_secret=github_webhook_secret,
            stripe_webhook_secret=stripe_webhook_secret,
        )
    )
    return TestClient(app), bus


class TestEmitReplayAuth:
    def test_emit_open_in_local_mode(self) -> None:
        client, bus = _client(api_keys={})  # no keys -> ANONYMOUS_LOCAL admin
        resp = client.post("/api/v1/events/emit", json={"topic": "t", "payload": {}})
        assert resp.status_code == 200
        assert bus.published and bus.published[0][0] == "t"

    def test_emit_requires_key_when_configured(self) -> None:
        client, bus = _client(api_keys={"adminkey": "admin"})
        resp = client.post("/api/v1/events/emit", json={"topic": "t", "payload": {}})
        assert resp.status_code == 401
        assert bus.published == []

    def test_emit_rejects_readonly_key(self) -> None:
        client, bus = _client(api_keys={"rokey": "readonly"})
        resp = client.post(
            "/api/v1/events/emit",
            json={"topic": "t", "payload": {}},
            headers={"Authorization": "Bearer rokey"},
        )
        assert resp.status_code == 403
        assert bus.published == []

    def test_emit_accepts_admin_key(self) -> None:
        client, bus = _client(api_keys={"adminkey": "admin"})
        resp = client.post(
            "/api/v1/events/emit",
            json={"topic": "t", "payload": {}},
            headers={"Authorization": "Bearer adminkey"},
        )
        assert resp.status_code == 200
        assert bus.published

    def test_replay_rejects_readonly_key(self) -> None:
        client, _bus = _client(api_keys={"rokey": "readonly"})
        resp = client.post(
            "/api/v1/events/evt-1/replay",
            headers={"Authorization": "Bearer rokey"},
        )
        assert resp.status_code == 403


class TestWebhookHmac:
    def test_open_source_without_secret(self) -> None:
        client, bus = _client(github_webhook_secret="")
        resp = client.post("/api/v1/webhooks/github", json={"a": 1})
        assert resp.status_code == 200
        assert bus.published and bus.published[0][0] == "webhook.github"

    def test_configured_source_requires_signature(self) -> None:
        client, bus = _client(github_webhook_secret="s3cret")
        resp = client.post("/api/v1/webhooks/github", json={"a": 1})
        assert resp.status_code == 401
        assert bus.published == []

    def test_configured_source_rejects_forged_signature(self) -> None:
        client, bus = _client(github_webhook_secret="s3cret")
        resp = client.post(
            "/api/v1/webhooks/github",
            json={"a": 1},
            headers={"X-Hub-Signature-256": "sha256=deadbeef"},
        )
        assert resp.status_code == 401
        assert bus.published == []

    def test_configured_source_accepts_valid_signature(self) -> None:
        client, bus = _client(github_webhook_secret="s3cret")
        body = json.dumps({"a": 1}).encode()
        sig = hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
        resp = client.post(
            "/api/v1/webhooks/github",
            content=body,
            headers={"X-Hub-Signature-256": f"sha256={sig}", "Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        assert bus.published and bus.published[0][0] == "webhook.github"

    def test_other_source_stays_open(self) -> None:
        client, bus = _client(github_webhook_secret="s3cret")
        resp = client.post("/api/v1/webhooks/stripe", json={"a": 1})
        assert resp.status_code == 200
        assert bus.published and bus.published[0][0] == "webhook.stripe"
