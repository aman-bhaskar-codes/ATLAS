"""Bounded structure-aware chunks with offsets into the canonical source text."""

from __future__ import annotations

import re

from atlas.knowledge.domain import FabricChunk, make_chunk_id
from atlas.knowledge.parsers import Parsed

DEFAULT_MAX_CHARS = 1200
DEFAULT_OVERLAP = 150
MAX_CHUNKS_PER_DOC = 400


def chunk_parsed(
    parsed: Parsed, document_id: str, *, max_chars: int = DEFAULT_MAX_CHARS, overlap: int = DEFAULT_OVERLAP
) -> list[FabricChunk]:
    """Keep sections/pages/tables together where possible; hard-bound every window.

    Chunks are exact substrings so offsets remain usable for page citations. A
    long sentence or paragraph still splits and zero overlap always advances.
    """
    if max_chars <= 0 or not 0 <= overlap < max_chars:
        raise ValueError("chunk size must be positive and 0 <= overlap < chunk size")
    text = parsed.text
    if not text.strip():
        return []
    regions: list[tuple[int, int, str]] = []
    if parsed.pages:
        regions = [(p.start, p.end, "") for p in parsed.pages]
    elif parsed.sections:
        start, heading = 0, ""
        for section in parsed.sections:
            regions.append((start, min(section.offset, len(text)), heading))
            start, heading = section.offset, section.heading
        regions.append((start, len(text), heading))
    else:
        regions = [(0, len(text), "")]

    chunks: list[FabricChunk] = []
    for lo, hi, heading in regions:
        # A table gets its own region. Ordinary paragraphs can share a window.
        cursor = lo
        for table in re.finditer(r"(?m)^\s*\|[^\n]*\|[^\n]*(?:\n\s*\|[^\n]*\|[^\n]*)*", text[lo:hi]):
            a, b = lo + table.start(), lo + table.end()
            _emit_windows(chunks, text, cursor, a, heading, document_id, max_chars, overlap)
            _emit_windows(chunks, text, a, b, heading, document_id, max_chars, overlap, kind="table")
            cursor = b
        _emit_windows(chunks, text, cursor, hi, heading, document_id, max_chars, overlap)
    total = len(chunks)
    return [c.model_copy(update={"total_chunks": total}) for c in chunks]


def _emit_windows(
    chunks: list[FabricChunk],
    text: str,
    start: int,
    stop: int,
    heading: str,
    document_id: str,
    size: int,
    overlap: int,
    *,
    kind: str = "text",
) -> None:
    while start < stop:
        while start < stop and text[start].isspace():
            start += 1
        if start >= stop:
            return
        end = min(start + size, stop)
        if end < stop:
            # Prefer paragraph/sentence/line/word boundaries in the latter half.
            floor = start + max(size // 2, overlap + 1)
            for marker in ("\n\n", ". ", "\n", " "):
                boundary = text.rfind(marker, floor, end)
                if boundary >= floor:
                    end = boundary + (1 if marker == ". " else 0)
                    break
        trimmed_end = end
        while trimmed_end > start and text[trimmed_end - 1].isspace():
            trimmed_end -= 1
        if trimmed_end > start:
            if len(chunks) >= MAX_CHUNKS_PER_DOC:
                raise ValueError("document exceeds chunk count limit")
            content = text[start:trimmed_end]
            idx = len(chunks)
            chunks.append(
                FabricChunk(
                    chunk_id=make_chunk_id(document_id, idx, content),
                    document_id=document_id,
                    content=content,
                    heading=heading,
                    chunk_index=idx,
                    char_start=start,
                    char_end=trimmed_end,
                    token_estimate=max(1, len(content) // 4),
                    kind=kind,
                )
            )
        if end >= stop:
            return
        start = max(start + 1, end - overlap)
