"""Structure-aware chunking (§18-21).

WHY structure-aware: fixed windows shred headings, tables, and functions. We
prefer boundaries in this order: sections (headings) → paragraphs → sentences,
keeping tables and code blocks whole whenever they fit the size budget.
"""

from __future__ import annotations

from atlas.knowledge.domain import FabricChunk, make_chunk_id
from atlas.knowledge.parsers import Parsed

DEFAULT_MAX_CHARS = 1200
DEFAULT_OVERLAP = 150
MAX_CHUNKS_PER_DOC = 400  # bounded index size per document


def _sentences(text: str) -> list[str]:
    out: list[str] = []
    current = ""
    for ch in text:
        current += ch
        if ch in ".!?\n" and len(current) > 40:
            out.append(current.strip())
            current = ""
    if current.strip():
        out.append(current.strip())
    return out


def _pack_units(units: list[str], *, max_chars: int, overlap: int) -> list[str]:
    """Greedy pack units into chunks of ≤ max_chars with char overlap."""
    packed: list[str] = []
    current = ""
    for unit in units:
        if not unit:
            continue
        if len(unit) > max_chars:  # giant unit: sentence-split fallback
            if current.strip():
                packed.append(current.strip())
                current = ""
            for sub in _sentences(unit):
                if len(current) + len(sub) + 1 > max_chars and current:
                    packed.append(current.strip())
                    current = current[-overlap:] if overlap else ""
                current = f"{current} {sub}".strip()
            continue
        if len(current) + len(unit) + 2 > max_chars and current:
            packed.append(current.strip())
            current = current[-overlap:] + "\n\n" + unit if overlap else unit
        else:
            current = f"{current}\n\n{unit}" if current else unit
    if current.strip():
        packed.append(current.strip())
    return packed


def _is_table_line(line: str) -> bool:
    s = line.strip()
    return s.startswith("|") and s.count("|") >= 2


def chunk_parsed(
    parsed: Parsed,
    document_id: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap: int = DEFAULT_OVERLAP,
) -> list[FabricChunk]:
    """Chunk parsed content into FabricChunks with heading provenance."""
    text = parsed.text
    if not text.strip():
        return []

    # 1. Split into sections at heading offsets when we have them.
    units_with_headings: list[tuple[str, str]] = []  # (heading, body)
    if parsed.sections:
        last = 0
        current_heading = ""
        # Sections are offsets of heading bodies; also find heading start lines.
        for sec in parsed.sections:
            body = text[last : sec.offset]
            if body.strip():
                units_with_headings.append((current_heading, body.strip()))
            current_heading = sec.heading
            last = sec.offset
        tail = text[last:]
        if tail.strip():
            units_with_headings.append((current_heading, tail.strip()))
    else:
        units_with_headings = [("", text)]

    # 2. Within each section, keep tables whole; split paragraphs otherwise.
    raw_units: list[tuple[str, str]] = []
    for heading, body in units_with_headings:
        lines = body.split("\n")
        buf: list[str] = []
        table: list[str] = []
        for line in lines:
            if _is_table_line(line):
                if buf:
                    raw_units.extend((heading, p) for p in _split_paragraphs("\n".join(buf)))
                    buf = []
                table.append(line)
            else:
                if table:
                    raw_units.append((heading, "\n".join(table)))
                    table = []
                buf.append(line)
        if table:
            raw_units.append((heading, "\n".join(table)))
        if buf:
            raw_units.extend((heading, p) for p in _split_paragraphs("\n".join(buf)))

    # 3. Pack into size-bounded chunks.
    chunks: list[FabricChunk] = []
    current_heading = ""
    pending: list[str] = []
    for heading, unit in raw_units:
        if heading and heading != current_heading and pending:
            chunks.extend(_emit(pending, current_heading, document_id, len(chunks), max_chars, overlap))
            pending = []
        current_heading = heading or current_heading
        pending.append(unit)
        if sum(len(p) for p in pending) > max_chars * 4:
            chunks.extend(_emit(pending, current_heading, document_id, len(chunks), max_chars, overlap))
            pending = []
    if pending:
        chunks.extend(_emit(pending, current_heading, document_id, len(chunks), max_chars, overlap))

    chunks = chunks[:MAX_CHUNKS_PER_DOC]
    total = len(chunks)
    return [
        c.model_copy(update={"total_chunks": total, "chunk_index": i}) for i, c in enumerate(chunks)
    ]


def _split_paragraphs(body: str) -> list[str]:
    return [p.strip() for p in body.split("\n\n") if p.strip()]


def _emit(
    units: list[str], heading: str, document_id: str, start_index: int, max_chars: int, overlap: int
) -> list[FabricChunk]:
    packed = _pack_units(units, max_chars=max_chars, overlap=overlap)
    out: list[FabricChunk] = []
    for i, text in enumerate(packed):
        idx = start_index + i
        out.append(
            FabricChunk(
                chunk_id=make_chunk_id(document_id, idx, text),
                document_id=document_id,
                content=text,
                heading=heading,
                chunk_index=idx,
                total_chunks=1,
                char_start=0,
                char_end=len(text),
                token_estimate=max(1, len(text) // 4),
                kind="table" if text.strip().startswith("|") else "text",
            )
        )
    return out
