"""Provider-native webhook signature verification.

WHY this module: the webhook ingestion endpoint authenticates with an ATLAS API
key (require_principal), but that only proves the *caller* holds a valid key —
not that the payload actually came from the provider. A stolen or guessed API
key lets an attacker forge ``webhook.github`` events onto the bus. Provider-
native HMAC verification closes this gap: with a secret configured, only payloads
the provider itself signed are accepted.

DESIGN DECISIONS:
- Exceptions, not booleans: ``verify()`` raises ``WebhookSignatureError`` on any
  failure — matching the codebase style (cf. ``require_principal`` raises
  HTTPException(401)). A boolean return invites unchecked code paths.
- ``hmac.compare_digest`` everywhere: mandatory constant-time comparison. A
  plain ``==`` on HMAC digests is a timing side-channel vulnerability.
- Secrets never logged: not the value, not the digest, not a prefix. Only the
  key name (e.g. ``GITHUB_WEBHOOK_SECRET``) may appear in log messages.
- Stripe replay window: 300 seconds (5 minutes). Stripe's own library uses 300s
  as the default tolerance; shorter windows cause false positives during
  provider-side retry storms.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from starlette.datastructures import Headers

from atlas.infra.logging import get_logger

if TYPE_CHECKING:
    pass

_log = get_logger("atlas.api.webhook_auth")

# Stripe replay tolerance in seconds. 300s matches Stripe's own default and
# survives provider-side retry storms without false positives.
STRIPE_TIMESTAMP_TOLERANCE_S = 300


class WebhookSignatureError(Exception):
    """Raised when provider-native signature verification fails.

    Carries a ``reason`` tag (``missing_signature``, ``bad_signature``,
    ``expired_timestamp``, ``malformed_header``) for audit logging.
    """

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(message)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class WebhookVerifier(Protocol):
    """Verify a provider's native webhook signature.

    Implementations MUST:
    - use ``hmac.compare_digest`` for every comparison;
    - raise ``WebhookSignatureError`` on any failure;
    - never log the secret value, the full signature, or request bodies.
    """

    def verify(self, body: bytes, headers: Headers, secret: str) -> None: ...


# ---------------------------------------------------------------------------
# GitHub — X-Hub-Signature-256  (HMAC-SHA256)
# ---------------------------------------------------------------------------


class GitHubVerifier:
    """Verify GitHub webhook signatures (``X-Hub-Signature-256``).

    GitHub sends ``sha256=<hex>`` in the header. The HMAC key is the webhook
    secret configured in the repository/organisation settings.
    """

    HEADER = "x-hub-signature-256"

    def verify(self, body: bytes, headers: Headers, secret: str) -> None:
        raw_sig = headers.get(self.HEADER)
        if not raw_sig:
            raise WebhookSignatureError(
                reason="missing_signature",
                message="X-Hub-Signature-256 header is required",
            )

        if not raw_sig.startswith("sha256="):
            raise WebhookSignatureError(
                reason="malformed_header",
                message="X-Hub-Signature-256 must start with sha256=",
            )

        provided_hex = raw_sig[len("sha256="):]
        expected_hex = hmac.new(
            secret.encode(), body, hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(expected_hex, provided_hex):
            raise WebhookSignatureError(
                reason="bad_signature",
                message="webhook signature mismatch",
            )


# ---------------------------------------------------------------------------
# Stripe — Stripe-Signature  (HMAC-SHA256 + timestamp replay protection)
# ---------------------------------------------------------------------------


class StripeVerifier:
    """Verify Stripe webhook signatures (``Stripe-Signature``).

    Stripe sends ``t=<unix_ts>,v1=<hex>[,v1=<hex>]``. Multiple ``v1`` values
    appear during secret rotation — accept if ANY match. The signed payload is
    ``f"{t}.".encode() + body``.

    Replay protection: reject when ``abs(now - t) > 300s``. The 300s window
    matches Stripe's own SDK default (``stripe.Webhook.DEFAULT_TOLERANCE``).
    """

    HEADER = "stripe-signature"

    def verify(self, body: bytes, headers: Headers, secret: str) -> None:
        raw_sig = headers.get(self.HEADER)
        if not raw_sig:
            raise WebhookSignatureError(
                reason="missing_signature",
                message="Stripe-Signature header is required",
            )

        # Parse the header into elements
        elements: dict[str, list[str]] = {}
        for part in raw_sig.split(","):
            part = part.strip()
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            elements.setdefault(key.strip(), []).append(value.strip())

        # Extract timestamp
        timestamps = elements.get("t", [])
        if not timestamps:
            raise WebhookSignatureError(
                reason="malformed_header",
                message="Stripe-Signature missing timestamp (t=…)",
            )

        try:
            ts = int(timestamps[0])
        except (ValueError, IndexError):
            raise WebhookSignatureError(
                reason="malformed_header",
                message="Stripe-Signature timestamp is not an integer",
            )

        # Replay protection
        age = abs(time.time() - ts)
        if age > STRIPE_TIMESTAMP_TOLERANCE_S:
            raise WebhookSignatureError(
                reason="expired_timestamp",
                message=f"Stripe-Signature timestamp too old ({int(age)}s > {STRIPE_TIMESTAMP_TOLERANCE_S}s)",
            )

        # Extract v1 signatures
        v1_sigs = elements.get("v1", [])
        if not v1_sigs:
            raise WebhookSignatureError(
                reason="malformed_header",
                message="Stripe-Signature missing v1 signature",
            )

        # Compute expected signature: sign "timestamp.body"
        signed_payload = f"{ts}.".encode() + body
        expected_hex = hmac.new(
            secret.encode(), signed_payload, hashlib.sha256
        ).hexdigest()

        # Accept if ANY v1 value matches (secret rotation)
        for v1 in v1_sigs:
            if hmac.compare_digest(expected_hex, v1):
                return

        raise WebhookSignatureError(
            reason="bad_signature",
            message="webhook signature mismatch",
        )


# ---------------------------------------------------------------------------
# Registry: source name → verifier instance
# ---------------------------------------------------------------------------

VERIFIERS: dict[str, WebhookVerifier] = {
    "github": GitHubVerifier(),
    "stripe": StripeVerifier(),
}

# Track unknown sources we've already warned about (once per process).
_warned_unknown_sources: set[str] = set()


def get_verifier(source: str) -> WebhookVerifier | None:
    """Look up the verifier for a webhook source.

    Unknown sources return ``None`` with a one-time warning log.
    """
    verifier = VERIFIERS.get(source)
    if verifier is None and source not in _warned_unknown_sources:
        _warned_unknown_sources.add(source)
        _log.warning(
            "events.webhook_unknown_source",
            event_type="api",
            source=source,
            detail="no verifier registered; passthrough with API-key gate only",
        )
    return verifier


def get_source_secret(settings: object, source: str) -> str:
    """Resolve the configured secret for a source from Settings.

    Returns the empty string when no secret is configured (verification
    inactive for that source).
    """
    attr_name = f"{source}_webhook_secret"
    return str(getattr(settings, attr_name, "") or "")
