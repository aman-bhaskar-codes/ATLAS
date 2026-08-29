"""Shared helpers for the scholarly knowledge providers.

WHY a helper module rather than a base class: the KnowledgeProvider contract is
a Protocol (structural), and every provider stays a flat, readable, independently
testable class. What genuinely repeats is *parsing* — dates in four different
shapes, author lists in three, OpenAlex's inverted abstract index — so only that
is shared.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

MAX_SNIPPET_CHARS = 1200


def clean_text(value: Any, *, limit: int = MAX_SNIPPET_CHARS) -> str:
    """Collapse whitespace and bound length. Scholarly abstracts run long."""
    if not value:
        return ""
    return " ".join(str(value).split())[:limit]


def parse_date(value: Any) -> datetime | None:
    """Parse the date shapes scholarly APIs actually return.

    Accepts ISO strings ("2024-03-11", "2024-03-11T09:00:00Z"), a bare year
    (int or "2024"), and Crossref's `date-parts` nesting. Returns None rather
    than raising: a missing date must never lose an otherwise-good paper.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, int):
        return _from_parts([value])
    if isinstance(value, list | tuple):
        parts = [int(p) for p in value if isinstance(p, int | float)]
        return _from_parts(parts)
    text = str(value).strip()
    if text.isdigit() and len(text) == 4:
        return _from_parts([int(text)])
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _from_parts(parts: list[int]) -> datetime | None:
    if not parts:
        return None
    year = parts[0]
    if not 1000 <= year <= 9999:
        return None
    month = parts[1] if len(parts) > 1 and 1 <= parts[1] <= 12 else 1
    day = parts[2] if len(parts) > 2 and 1 <= parts[2] <= 31 else 1
    try:
        return datetime(year, month, day, tzinfo=UTC)
    except ValueError:
        return datetime(year, 1, 1, tzinfo=UTC)


def normalize_doi(value: Any) -> str:
    """Strip the resolver prefix so `doi` is comparable across providers."""
    doi = str(value or "").strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.lower().startswith(prefix):
            doi = doi[len(prefix) :]
            break
    return doi.strip("/")


def authors_from_openalex(authorships: Any, *, limit: int = 12) -> tuple[str, ...]:
    out: list[str] = []
    if not isinstance(authorships, list):
        return ()
    for entry in authorships[:limit]:
        if not isinstance(entry, dict):
            continue
        author = entry.get("author")
        name = author.get("display_name") if isinstance(author, dict) else None
        if name:
            out.append(clean_text(name, limit=120))
    return tuple(out)


def authors_from_crossref(authors: Any, *, limit: int = 12) -> tuple[str, ...]:
    out: list[str] = []
    if not isinstance(authors, list):
        return ()
    for entry in authors[:limit]:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name") or " ".join(
            part for part in (entry.get("given"), entry.get("family")) if isinstance(part, str) and part
        )
        if name:
            out.append(clean_text(name, limit=120))
    return tuple(out)


def authors_from_names(authors: Any, *, key: str = "name", limit: int = 12) -> tuple[str, ...]:
    out: list[str] = []
    if not isinstance(authors, list):
        return ()
    for entry in authors[:limit]:
        name = entry.get(key) if isinstance(entry, dict) else entry
        if isinstance(name, str) and name.strip():
            out.append(clean_text(name, limit=120))
    return tuple(out)


def abstract_from_inverted_index(index: Any, *, limit: int = MAX_SNIPPET_CHARS) -> str:
    """Rebuild an OpenAlex abstract from its inverted position index.

    OpenAlex ships abstracts as {token: [positions]} for licensing reasons.
    Reconstruction is exact, cheap and deterministic.
    """
    if not isinstance(index, dict) or not index:
        return ""
    slots: list[tuple[int, str]] = []
    for token, positions in index.items():
        if not isinstance(positions, list):
            continue
        for pos in positions:
            if isinstance(pos, int):
                slots.append((pos, str(token)))
    if not slots:
        return ""
    slots.sort(key=lambda pair: pair[0])
    return clean_text(" ".join(token for _, token in slots), limit=limit)


def strip_markup(value: Any, *, limit: int = MAX_SNIPPET_CHARS) -> str:
    """Crossref abstracts are JATS XML; keep the prose, drop the tags."""
    import re

    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return clean_text(text, limit=limit)
