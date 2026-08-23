"""Stable error fingerprints (Prompt 5 §14).

WHY a fingerprint and not the message: the same bug produces slightly different
messages every time — a different task id, a different retry count, a different
temp path. Grouping on the raw message creates one "incident" per occurrence,
which is exactly what §13 forbids. Grouping on `(file, line)` is no better,
because inserting a blank line above a raise site silently splits an incident's
history in two.

WHAT IS AND IS NOT IN THE FINGERPRINT

In: the error's declared `code` (from `atlas.infra.errors.AtlasError`, a
human-chosen constant), the exception type name, the component, and the message
with all volatile substrings normalised away.

Out: line numbers, timestamps, ids, addresses, absolute paths, counts, and
durations. Every one of those changes between two occurrences of the same bug.

WHY the code comes first: `AtlasError` subclasses already declare a stable
`code` class attribute. When one is present it is the strongest available
discriminator and needs no heuristics at all.
"""

from __future__ import annotations

import hashlib
import re

#: Longest normalised message we hash. A 40 KB traceback and its 39 KB cousin
#: should not be different incidents because they diverge at character 38 000.
_MAX_MESSAGE_CHARS = 400

_FINGERPRINT_HEX_LEN = 16

# Order matters: the more specific patterns must run before the bare-number rule,
# or `0x7f3a` becomes `0xN` via the number rule and loses its shape.
_NORMALISERS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Memory addresses: <object at 0x7f3a9c1d2e40>
    (re.compile(r"0x[0-9a-fA-F]+"), "<addr>"),
    # UUIDs, with or without dashes.
    (
        re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"),
        "<uuid>",
    ),
    # ATLAS ids: inc_a1b2c3d4e5f6, hyp_…, exp_…, task_… — prefix keeps meaning,
    # the hex does not.
    (re.compile(r"\b([a-z][a-z0-9]{1,15})_[0-9a-f]{6,}\b"), r"\1_<id>"),
    # ISO-8601 timestamps.
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"), "<ts>"),
    # Absolute POSIX paths → basename only, so /tmp/pytest-of-x/test_a0/db.sqlite
    # and /Users/…/db.sqlite fingerprint alike.
    (re.compile(r"(?:/[\w.\-]+){2,}/([\w.\-]+)"), r"<path>/\1"),
    # Bare hex runs (hashes, digests) that survived the rules above.
    (re.compile(r"\b[0-9a-f]{12,}\b"), "<hex>"),
    # Durations and sizes before plain numbers, so the unit survives.
    (re.compile(r"\b\d+(?:\.\d+)?\s*(ms|s|us|ns|kb|mb|gb|b)\b", re.IGNORECASE), r"<n><\1>"),
    # Everything numeric left over.
    (re.compile(r"\b\d+(?:\.\d+)?\b"), "<n>"),
    # Collapse whitespace last.
    (re.compile(r"\s+"), " "),
)


def normalise_message(message: str) -> str:
    """Strip every substring that varies between occurrences of the same bug.

    Deterministic and pure — the same input always yields the same output, which
    is what makes a fingerprint reproducible across restarts.
    """
    text = message.strip()
    for pattern, replacement in _NORMALISERS:
        text = pattern.sub(replacement, text)
    return text[:_MAX_MESSAGE_CHARS].strip()


def fingerprint(
    *,
    source: str,
    component: str = "",
    code: str = "",
    exception_type: str = "",
    message: str = "",
    extra: tuple[str, ...] = (),
) -> str:
    """A stable id for "this kind of problem, in this place".

    `source` is the `IncidentSource` value — passed as a plain string so this
    module stays importable by anything, including `infra`-level callers that must
    not import the engineering domain.

    `extra` is for discriminators a detector knows about and the message does not
    (a worker name, a provider, a table name). Keep it stable: putting a counter
    in `extra` defeats the whole mechanism.
    """
    parts = (
        source.strip(),
        component.strip(),
        code.strip(),
        exception_type.strip(),
        normalise_message(message),
        *(e.strip() for e in extra),
    )
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return f"fp_{digest[:_FINGERPRINT_HEX_LEN]}"


def fingerprint_exception(
    exc: BaseException,
    *,
    source: str,
    component: str = "",
    extra: tuple[str, ...] = (),
) -> str:
    """Fingerprint a live exception.

    Reads `code` off the exception when it is an `AtlasError` — that attribute is
    a class constant, so it is the most stable discriminator available. Uses
    `getattr` rather than importing `AtlasError` so this helper also works for
    third-party and builtin exceptions without a type check.
    """
    code = getattr(exc, "code", "")
    return fingerprint(
        source=source,
        component=component,
        code=code if isinstance(code, str) else "",
        exception_type=type(exc).__name__,
        message=str(exc),
        extra=extra,
    )


__all__ = ["fingerprint", "fingerprint_exception", "normalise_message"]
