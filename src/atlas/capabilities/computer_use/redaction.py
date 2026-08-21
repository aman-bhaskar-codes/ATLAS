"""Credential protection for perception (Phase 17).

Perception sees everything on screen — including passwords, OTPs, API keys.
ATLAS must never store secrets in screenshots, expose them to logs, send them
to external models, or persist them as ordinary memory.

Pipeline: detect sensitive fields -> mark PerceivedElement.sensitive ->
redact values before any model-facing rendering. Redaction is applied at the
snapshot boundary, so the RAW snapshot (kept for local verification) and the
REDACTED snapshot (sent to models) are distinct objects.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from atlas.perception.contracts import PerceivedElement, PerceptionSnapshot

# Field-level detection: labels/hints that indicate credential entry.
_SENSITIVE_FIELD_HINTS: tuple[str, ...] = (
    "password",
    "passwd",
    "passcode",
    "secret",
    "api key",
    "apikey",
    "api_key",
    "token",
    "otp",
    "one-time",
    "verification code",
    "credit card",
    "card number",
    "cvv",
    "cvc",
    "private key",
    "pin",
    "ssn",
)

# Value-level detection: shapes that look like secrets regardless of label.
_SECRET_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]+", re.IGNORECASE),
    re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),  # JWT
    re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"),  # OpenAI-style keys
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),  # GitHub PAT
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b(?:\d[ -]?){13,19}\b"),  # PAN-like digit runs
)

_REDACTED = "[REDACTED]"


@dataclass(frozen=True)
class RedactionPolicy:
    """What to redact. Defaults are maximal protection."""

    redact_field_values: bool = True  # values of sensitive fields
    redact_secret_shapes: bool = True  # JWT/Bearer/key shapes anywhere in text
    allow_visual_for_sensitive: bool = False  # block screenshots of sensitive surfaces


def is_sensitive_field(label: str | None, value: str | None = None) -> bool:
    """Detect a sensitive input field from its accessible name."""
    haystack = f"{label or ''}".lower()
    return any(hint in haystack for hint in _SENSITIVE_FIELD_HINTS)


def contains_secret_shape(text: str) -> bool:
    return any(p.search(text) for p in _SECRET_VALUE_PATTERNS)


def redact_snapshot(snapshot: PerceptionSnapshot, *, policy: RedactionPolicy | None = None) -> PerceptionSnapshot:
    """Return a model-safe copy of the snapshot. The original is untouched."""
    policy = policy or RedactionPolicy()

    elements: list[PerceivedElement] = []
    for el in snapshot.elements:
        sensitive = el.sensitive or is_sensitive_field(el.label)
        value = el.value
        if policy.redact_field_values and sensitive and value:
            value = _REDACTED
        elif policy.redact_secret_shapes and value and contains_secret_shape(value):
            value = _REDACTED
            sensitive = True
        if sensitive or value != el.value:
            el = el.model_copy(update={"sensitive": sensitive, "value": value})
        elements.append(el)

    text = snapshot.text
    if policy.redact_secret_shapes and text and contains_secret_shape(text):
        for pattern in _SECRET_VALUE_PATTERNS:
            text = pattern.sub(_REDACTED, text)

    visual = snapshot.visual
    if snapshot.sensitive and visual is not None and not policy.allow_visual_for_sensitive:
        # Never hand pixels of a sensitive surface to a model (Phase 17/21).
        visual = None

    return snapshot.model_copy(
        update={
            "elements": tuple(elements),
            "text": text,
            "visual": visual,
            "metadata": {**snapshot.metadata, "redacted": "true"},
        }
    )
