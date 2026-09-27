"""Provider-native webhook signature verification tests.

Covers:
  a. GitHub: valid signature → 200; wrong signature → 401; missing header → 401;
     tampered body → 401; malformed header (no sha256= prefix) → 401.
  b. Stripe: valid → 200; timestamp 301+s old → 401 (replay); wrong v1 → 401;
     multiple v1 values with one valid → 200.
  c. No secret configured → 200 with API key alone (today's behaviour preserved);
     assert a "verification off" debug log is emitted.
  d. Unknown source (e.g. source=acme) → passthrough, no 500.
  e. Raw-body availability: assert the handler's parsed payload equals the body
     the verifier saw (dependency + FastAPI parsing coexist).
  f. Secret hygiene: capture log output across all failure tests and assert the
     secret value never appears; assert compare_digest is the comparison used.
  g. All existing routes_events tests pass unchanged.
"""

from __future__ import annotations

import hashlib
import hmac
import inspect
import json
import logging
import time
import unittest.mock
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

from atlas.interfaces.api import routes_events
from atlas.interfaces.api.webhook_auth import (
    GitHubVerifier,
    StripeVerifier,
    WebhookSignatureError,
    get_source_secret,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
    routes_events._deps = SimpleNamespace(manager=None, db=None, bus=bus)  # type: ignore[assignment]
    app.state.api_keys = api_keys or {}
    app.state.atlas = SimpleNamespace(
        settings=SimpleNamespace(
            github_webhook_secret=github_webhook_secret,
            stripe_webhook_secret=stripe_webhook_secret,
        )
    )
    return TestClient(app), bus


def _github_sign(body: bytes, secret: str) -> str:
    """Produce a valid X-Hub-Signature-256 header value."""
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


def _stripe_sign(body: bytes, secret: str, ts: int | None = None) -> tuple[str, int]:
    """Produce a valid Stripe-Signature header value + timestamp."""
    if ts is None:
        ts = int(time.time())
    signed_payload = f"{ts}.".encode() + body
    sig = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}", ts


# ===========================================================================
# a. GitHub verifier
# ===========================================================================


class TestGitHubVerifier:
    SECRET = "gh_test_secret_42"

    def test_valid_signature_200(self) -> None:
        client, bus = _client(github_webhook_secret=self.SECRET)
        body = json.dumps({"action": "push"}).encode()
        sig = _github_sign(body, self.SECRET)
        resp = client.post(
            "/api/v1/webhooks/github",
            content=body,
            headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert bus.published and bus.published[0][0] == "webhook.github"

    def test_wrong_signature_401(self) -> None:
        client, bus = _client(github_webhook_secret=self.SECRET)
        body = json.dumps({"action": "push"}).encode()
        resp = client.post(
            "/api/v1/webhooks/github",
            content=body,
            headers={"X-Hub-Signature-256": "sha256=deadbeef", "Content-Type": "application/json"},
        )
        assert resp.status_code == 401
        assert bus.published == []

    def test_missing_header_401(self) -> None:
        client, bus = _client(github_webhook_secret=self.SECRET)
        resp = client.post("/api/v1/webhooks/github", json={"a": 1})
        assert resp.status_code == 401
        assert bus.published == []

    def test_tampered_body_401(self) -> None:
        client, bus = _client(github_webhook_secret=self.SECRET)
        body = json.dumps({"action": "push"}).encode()
        sig = _github_sign(body, self.SECRET)
        # Flip one byte in the body
        tampered = body[:-1] + bytes([body[-1] ^ 0xFF])
        resp = client.post(
            "/api/v1/webhooks/github",
            content=tampered,
            headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"},
        )
        assert resp.status_code == 401
        assert bus.published == []

    def test_malformed_header_no_prefix_401(self) -> None:
        client, bus = _client(github_webhook_secret=self.SECRET)
        body = json.dumps({"action": "push"}).encode()
        # Missing sha256= prefix
        sig = hmac.new(self.SECRET.encode(), body, hashlib.sha256).hexdigest()
        resp = client.post(
            "/api/v1/webhooks/github",
            content=body,
            headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"},
        )
        assert resp.status_code == 401
        assert bus.published == []


# ===========================================================================
# b. Stripe verifier
# ===========================================================================


class TestStripeVerifier:
    SECRET = "whsec_test_stripe_secret_99"

    def test_valid_signature_200(self) -> None:
        client, bus = _client(stripe_webhook_secret=self.SECRET)
        body = json.dumps({"type": "payment_intent.succeeded"}).encode()
        sig_header, _ = _stripe_sign(body, self.SECRET)
        resp = client.post(
            "/api/v1/webhooks/stripe",
            content=body,
            headers={"Stripe-Signature": sig_header, "Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert bus.published and bus.published[0][0] == "webhook.stripe"

    def test_expired_timestamp_401(self) -> None:
        client, bus = _client(stripe_webhook_secret=self.SECRET)
        body = json.dumps({"type": "charge.failed"}).encode()
        # Timestamp 301s in the past
        old_ts = int(time.time()) - 301
        sig_header, _ = _stripe_sign(body, self.SECRET, ts=old_ts)
        resp = client.post(
            "/api/v1/webhooks/stripe",
            content=body,
            headers={"Stripe-Signature": sig_header, "Content-Type": "application/json"},
        )
        assert resp.status_code == 401
        assert bus.published == []

    def test_wrong_v1_401(self) -> None:
        client, bus = _client(stripe_webhook_secret=self.SECRET)
        body = json.dumps({"type": "charge.failed"}).encode()
        ts = int(time.time())
        resp = client.post(
            "/api/v1/webhooks/stripe",
            content=body,
            headers={"Stripe-Signature": f"t={ts},v1=deadbeef", "Content-Type": "application/json"},
        )
        assert resp.status_code == 401
        assert bus.published == []

    def test_multiple_v1_one_valid_200(self) -> None:
        client, bus = _client(stripe_webhook_secret=self.SECRET)
        body = json.dumps({"type": "invoice.paid"}).encode()
        ts = int(time.time())
        signed_payload = f"{ts}.".encode() + body
        valid_sig = hmac.new(self.SECRET.encode(), signed_payload, hashlib.sha256).hexdigest()
        # Header with one invalid v1 and one valid v1
        sig_header = f"t={ts},v1=invalid_old_sig,v1={valid_sig}"
        resp = client.post(
            "/api/v1/webhooks/stripe",
            content=body,
            headers={"Stripe-Signature": sig_header, "Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        assert bus.published and bus.published[0][0] == "webhook.stripe"

    def test_missing_stripe_signature_header_401(self) -> None:
        client, bus = _client(stripe_webhook_secret=self.SECRET)
        resp = client.post("/api/v1/webhooks/stripe", json={"a": 1})
        assert resp.status_code == 401
        assert bus.published == []


# ===========================================================================
# c. No secret configured → today's behaviour
# ===========================================================================


class TestNoSecretConfigured:
    def test_github_no_secret_200(self) -> None:
        client, bus = _client(github_webhook_secret="")
        resp = client.post("/api/v1/webhooks/github", json={"action": "push"})
        assert resp.status_code == 200
        assert bus.published and bus.published[0][0] == "webhook.github"

    def test_stripe_no_secret_200(self) -> None:
        client, bus = _client(stripe_webhook_secret="")
        resp = client.post("/api/v1/webhooks/stripe", json={"type": "charge.succeeded"})
        assert resp.status_code == 200
        assert bus.published and bus.published[0][0] == "webhook.stripe"

    def test_verification_off_debug_log(self) -> None:
        client, bus = _client(github_webhook_secret="")
        with unittest.mock.patch("atlas.interfaces.api.routes_events._log.debug") as mock_debug:
            client.post("/api/v1/webhooks/github", json={"a": 1})
        mock_debug.assert_called_with(
            "events.webhook_verification_off",
            event_type="api",
            source="github",
        )


# ===========================================================================
# d. Unknown source → passthrough
# ===========================================================================


class TestUnknownSource:
    def test_unknown_source_passthrough(self) -> None:
        client, bus = _client()
        resp = client.post("/api/v1/webhooks/acme", json={"x": 1})
        assert resp.status_code == 200
        assert bus.published and bus.published[0][0] == "webhook.acme"

    def test_unknown_source_no_500(self) -> None:
        client, _bus = _client()
        resp = client.post("/api/v1/webhooks/totally_unknown_provider", json={})
        assert resp.status_code == 200


# ===========================================================================
# e. Raw-body availability
# ===========================================================================


class TestRawBodyAvailability:
    def test_parsed_payload_equals_raw(self) -> None:
        """Assert the handler's parsed payload equals the body the verifier saw."""
        client, bus = _client(github_webhook_secret="secret_e")
        payload = {"nested": {"key": "value"}, "list": [1, 2, 3]}
        body = json.dumps(payload).encode()
        sig = _github_sign(body, "secret_e")
        resp = client.post(
            "/api/v1/webhooks/github",
            content=body,
            headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        # The bus received the parsed event with matching fields
        assert bus.published
        topic, event = bus.published[0]
        assert topic == "webhook.github"
        # Pydantic model should have absorbed the payload fields
        assert event.nested == {"key": "value"}
        assert event.list == [1, 2, 3]


# ===========================================================================
# f. Secret hygiene
# ===========================================================================


class TestSecretHygiene:
    SECRET = "ultra_secret_never_log_this"

    def test_secret_never_in_logs_github(self) -> None:
        client, _bus = _client(github_webhook_secret=self.SECRET)
        with unittest.mock.patch("atlas.interfaces.api.routes_events._log.warning") as mock_warn:
            client.post(
                "/api/v1/webhooks/github",
                json={"a": 1},
                headers={"X-Hub-Signature-256": "sha256=deadbeef"},
            )
        # Verify the secret is not passed in the log arguments
        for call in mock_warn.mock_calls:
            assert self.SECRET not in str(call)

    def test_secret_never_in_logs_stripe(self) -> None:
        client, _bus = _client(stripe_webhook_secret=self.SECRET)
        ts = int(time.time())
        with unittest.mock.patch("atlas.interfaces.api.routes_events._log.warning") as mock_warn:
            client.post(
                "/api/v1/webhooks/stripe",
                json={"a": 1},
                headers={"Stripe-Signature": f"t={ts},v1=deadbeef"},
            )
        for call in mock_warn.mock_calls:
            assert self.SECRET not in str(call)

    def test_compare_digest_used_in_github_verifier(self) -> None:
        """Inspect source to confirm hmac.compare_digest is used."""
        source = inspect.getsource(GitHubVerifier.verify)
        assert "compare_digest" in source
        # Must NOT use plain == for digest comparison
        # (We check the source contains compare_digest, which is the safe path)

    def test_compare_digest_used_in_stripe_verifier(self) -> None:
        source = inspect.getsource(StripeVerifier.verify)
        assert "compare_digest" in source


# ===========================================================================
# Pure unit tests for verifier classes (no FastAPI)
# ===========================================================================


class TestGitHubVerifierUnit:
    def test_raises_on_missing_header(self) -> None:
        v = GitHubVerifier()
        with pytest.raises(WebhookSignatureError, match="required") as exc_info:
            v.verify(b"body", Headers(), "secret")
        assert exc_info.value.reason == "missing_signature"

    def test_raises_on_malformed_header(self) -> None:
        v = GitHubVerifier()
        headers = Headers({"x-hub-signature-256": "md5=abc"})
        with pytest.raises(WebhookSignatureError, match="sha256=") as exc_info:
            v.verify(b"body", headers, "secret")
        assert exc_info.value.reason == "malformed_header"

    def test_raises_on_bad_signature(self) -> None:
        v = GitHubVerifier()
        headers = Headers({"x-hub-signature-256": "sha256=0000"})
        with pytest.raises(WebhookSignatureError, match="mismatch") as exc_info:
            v.verify(b"body", headers, "secret")
        assert exc_info.value.reason == "bad_signature"

    def test_valid_passes(self) -> None:
        v = GitHubVerifier()
        body = b'{"test": true}'
        secret = "my_secret"
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        headers = Headers({"x-hub-signature-256": f"sha256={sig}"})
        v.verify(body, headers, secret)  # should not raise


class TestStripeVerifierUnit:
    def test_raises_on_missing_header(self) -> None:
        v = StripeVerifier()
        with pytest.raises(WebhookSignatureError, match="required") as exc_info:
            v.verify(b"body", Headers(), "secret")
        assert exc_info.value.reason == "missing_signature"

    def test_raises_on_missing_timestamp(self) -> None:
        v = StripeVerifier()
        headers = Headers({"stripe-signature": "v1=abc"})
        with pytest.raises(WebhookSignatureError, match="timestamp") as exc_info:
            v.verify(b"body", headers, "secret")
        assert exc_info.value.reason == "malformed_header"

    def test_raises_on_expired_timestamp(self) -> None:
        v = StripeVerifier()
        old_ts = int(time.time()) - 400
        body = b"body"
        signed_payload = f"{old_ts}.".encode() + body
        sig = hmac.new(b"secret", signed_payload, hashlib.sha256).hexdigest()
        headers = Headers({"stripe-signature": f"t={old_ts},v1={sig}"})
        with pytest.raises(WebhookSignatureError, match="too old") as exc_info:
            v.verify(body, headers, "secret")
        assert exc_info.value.reason == "expired_timestamp"

    def test_raises_on_missing_v1(self) -> None:
        v = StripeVerifier()
        ts = int(time.time())
        headers = Headers({"stripe-signature": f"t={ts}"})
        with pytest.raises(WebhookSignatureError, match="v1") as exc_info:
            v.verify(b"body", headers, "secret")
        assert exc_info.value.reason == "malformed_header"

    def test_valid_passes(self) -> None:
        v = StripeVerifier()
        body = b'{"type": "test"}'
        secret = "stripe_secret"
        ts = int(time.time())
        signed_payload = f"{ts}.".encode() + body
        sig = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
        headers = Headers({"stripe-signature": f"t={ts},v1={sig}"})
        v.verify(body, headers, secret)  # should not raise


# ===========================================================================
# get_source_secret helper
# ===========================================================================


class TestGetSourceSecret:
    def test_returns_secret_when_present(self) -> None:
        settings = SimpleNamespace(github_webhook_secret="abc123")
        assert get_source_secret(settings, "github") == "abc123"

    def test_returns_empty_when_missing(self) -> None:
        settings = SimpleNamespace()
        assert get_source_secret(settings, "github") == ""

    def test_returns_empty_for_none(self) -> None:
        settings = SimpleNamespace(github_webhook_secret=None)
        assert get_source_secret(settings, "github") == ""
