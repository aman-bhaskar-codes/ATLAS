"""Structure parsers — raw bytes → normalized text + structural hints (§22).

Local-first: everything here is deterministic, dependency-free, and cheap.
Parsers emit `Parsed(text, title, sections)` where `sections` carries heading
boundaries the chunker uses to avoid splitting inside a logical section.
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from typing import Any

MAX_PARSED_CHARS = 400_000  # bounded parse; oversize content is truncated, never OOMs


@dataclass(frozen=True)
class Section:
    heading: str
    level: int
    offset: int  # char offset of the section body in the parsed text


@dataclass(frozen=True)
class Parsed:
    text: str
    title: str = ""
    sections: tuple[Section, ...] = ()
    kind: str = "text"  # text | markdown | html | json | csv | code | yaml
    metadata: dict[str, Any] = field(default_factory=dict)


def _truncate(text: str) -> str:
    return text[:MAX_PARSED_CHARS]


def parse_text(content: str, *, title: str = "") -> Parsed:
    return Parsed(text=_truncate(content), title=title, kind="text")


_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


def parse_markdown(content: str, *, title: str = "") -> Parsed:
    sections: list[Section] = []
    found_title = title
    for m in _MD_HEADING.finditer(content):
        level = len(m.group(1))
        heading = m.group(2).strip()
        if not found_title and level == 1:
            found_title = heading
        sections.append(Section(heading=heading, level=level, offset=m.end()))
    return Parsed(text=_truncate(content), title=found_title, sections=tuple(sections), kind="markdown")


_HTML_BLOCK = re.compile(r"<(script|style|nav|header|footer|aside)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_HTML_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.DOTALL | re.IGNORECASE)
_HTML_HEAD = re.compile(r"<h([1-6])[^>]*>(.*?)</h\1>", re.DOTALL | re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")


def _strip_tags(html: str) -> str:
    text = _TAG.sub(" ", html)
    return re.sub(r"\s+", " ", text).strip()


def parse_html(content: str, *, title: str = "") -> Parsed:
    found_title = title
    m = _HTML_TITLE.search(content)
    if not found_title and m:
        found_title = _strip_tags(m.group(1))
    body = _HTML_BLOCK.sub(" ", content)
    # Keep heading markers so the chunker can respect section boundaries.
    def _head_repl(hm: re.Match[str]) -> str:
        return f"\n\n{'#' * int(hm.group(1))} {_strip_tags(hm.group(2))}\n\n"

    with_heads = _HTML_HEAD.sub(_head_repl, body)
    text = _TAG.sub(" ", with_heads)
    # collapse runs of spaces but KEEP newlines so '# Heading' lines survive
    # for the markdown pass below (section boundaries for the chunker).
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text).strip()
    if "#" in text:
        return parse_markdown(text, title=found_title)
    return Parsed(text=_truncate(text), title=found_title, kind="html")


def _flatten_json(value: Any, prefix: str, out: list[str], *, depth: int = 0) -> None:
    """Flatten JSON into 'path: value' lines. Bounded: depth ≤ 6, lists ≤ 25."""
    if depth > 6 or len(out) > 2000:
        return
    if isinstance(value, dict):
        for k, v in list(value.items())[:100]:
            _flatten_json(v, f"{prefix}.{k}" if prefix else str(k), out, depth=depth + 1)
    elif isinstance(value, list):
        for i, v in enumerate(value[:25]):
            _flatten_json(v, f"{prefix}[{i}]", out, depth=depth + 1)
    else:
        out.append(f"{prefix}: {value}")


def parse_json(content: str, *, title: str = "") -> Parsed:
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return parse_text(content, title=title)
    lines: list[str] = []
    _flatten_json(data, "", lines)
    text = "\n".join(lines)
    return Parsed(text=_truncate(text), title=title, kind="json")


def parse_csv(content: str, *, title: str = "") -> Parsed:
    """Render CSV as 'col=value' lines per row; keep tables readable (§21)."""
    try:
        reader = csv.reader(io.StringIO(content))
        rows = list(reader)[:500]
    except csv.Error:
        return parse_text(content, title=title)
    if not rows:
        return parse_text(content, title=title)
    header = rows[0]
    lines: list[str] = []
    for row in rows[1:]:
        pairs = [f"{h}={v}" for h, v in zip(header, row, strict=False)]
        lines.append("; ".join(pairs))
    return Parsed(text=_truncate("\n".join(lines)), title=title, kind="csv")


_CODE_DEF = re.compile(
    r"^\s*(?:def |class |async def )\w+|^function \w+|^(?:export )?(?:const|let|var) \w+", re.MULTILINE
)


def parse_code(content: str, *, title: str = "") -> Parsed:
    sections = [Section(heading=m.group(0).strip(), level=2, offset=m.end()) for m in _CODE_DEF.finditer(content)]
    return Parsed(text=_truncate(content), title=title, sections=tuple(sections[:500]), kind="code")


def parse_yaml(content: str, *, title: str = "") -> Parsed:
    # YAML is kept verbatim — line structure IS its semantics.
    return Parsed(text=_truncate(content), title=title, kind="yaml")


_EXT_KIND: dict[str, str] = {
    ".md": "markdown",
    ".markdown": "markdown",
    ".html": "html",
    ".htm": "html",
    ".json": "json",
    ".csv": "csv",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".py": "code",
    ".js": "code",
    ".ts": "code",
    ".tsx": "code",
    ".jsx": "code",
    ".go": "code",
    ".rs": "code",
    ".java": "code",
    ".sh": "code",
    ".sql": "code",
    ".css": "code",
}


def parse_content(content: str, *, content_type: str = "", uri: str = "", title: str = "") -> Parsed:
    """Dispatch by content_type, then URI extension, then sniffing."""
    kind = ""
    if "markdown" in content_type:
        kind = "markdown"
    elif "html" in content_type:
        kind = "html"
    elif "json" in content_type:
        kind = "json"
    elif "csv" in content_type:
        kind = "csv"
    if not kind and uri:
        dot = uri.rfind(".")
        if dot != -1:
            kind = _EXT_KIND.get(uri[dot:].split("?")[0].lower(), "")
    if not kind:
        head = content[:200]
        if head.lstrip().startswith(("<!DOCTYPE", "<html", "<div", "<p")):
            kind = "html"
        elif head.lstrip().startswith(("{", "[")):
            kind = "json"
        elif re.search(r"^#{1,6} ", content[:2000], re.MULTILINE):
            kind = "markdown"
        else:
            kind = "text"
    if kind == "markdown":
        return parse_markdown(content, title=title)
    if kind == "html":
        return parse_html(content, title=title)
    if kind == "json":
        return parse_json(content, title=title)
    if kind == "csv":
        return parse_csv(content, title=title)
    if kind == "yaml":
        return parse_yaml(content, title=title)
    if kind == "code":
        return parse_code(content, title=title)
    return parse_text(content, title=title)
