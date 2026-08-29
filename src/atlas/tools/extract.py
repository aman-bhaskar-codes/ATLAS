"""Structure-aware, dependency-free text extraction (§6, §7).

WHY this file exists: `read_url` and the browser reader used to run a naive
tag-stripping regex ("Extracted text placeholder") and DECLINED every PDF. That
made "read this paper" a dead end for the most common research artifact. This
module gives ATLAS a real, local-first extraction path with ZERO new
dependencies — pure stdlib (`re`, `html`, `zlib`) — honouring the fabric's
"deterministic, dependency-free, cheap; degrade instead of crashing" rule.

Layering: this lives in `atlas.tools`, so `HttpTextFetcher` (same layer) and the
browser `ReaderEngine` (a higher layer) can both reuse it without either owning a
document engine. It NEVER raises past its boundary: a page it cannot read yields
empty text, which callers surface as "no extractable text" rather than garbage.

Trust: output is DATA (§23). Extraction produces text for the model to reason
over and cite; it grants no authority and executes nothing a document suggests.
"""

from __future__ import annotations

import html as _htmllib
import re
import zlib

MAX_EXTRACT_CHARS = 400_000  # matches knowledge.parsers.MAX_PARSED_CHARS

# Blocks whose contents are chrome, not article prose. Dropped whole (§7 "canonicalize").
_BOILERPLATE = re.compile(
    r"<(script|style|noscript|nav|header|footer|aside|form|template|svg)[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
# Heading open tags → markdown markers so section boundaries survive for the chunker.
_HEADING = re.compile(r"<h([1-6])[^>]*>(.*?)</h\1>", re.IGNORECASE | re.DOTALL)
# Block closers that imply a line break in the flattened text.
_BLOCK_BREAK = re.compile(r"</p>|</div>|</li>|<br\s*/?>|</tr>|</blockquote>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
_MANY_BLANK = re.compile(r"\n{3,}")


def _clean_inline(fragment: str) -> str:
    """Strip tags + unescape entities inside a heading/title fragment."""
    return _htmllib.unescape(" ".join(_TAG.sub(" ", fragment).split()))


def html_to_text(html: str, *, title_hint: str = "") -> tuple[str, str]:
    """HTML → (title, structured plain text).

    Boilerplate blocks are removed; headings become `#`-prefixed markers so the
    downstream chunker respects section boundaries; entities are unescaped.
    """
    if not html:
        return (title_hint, "")

    title = title_hint
    if not title:
        m = _TITLE.search(html) or _H1.search(html)
        if m:
            title = _clean_inline(m.group(1))

    body = _BOILERPLATE.sub(" ", html)

    def _heading_repl(hm: re.Match[str]) -> str:
        level = int(hm.group(1))
        return f"\n\n{'#' * level} {_clean_inline(hm.group(2))}\n\n"

    body = _HEADING.sub(_heading_repl, body)
    body = _BLOCK_BREAK.sub("\n", body)
    body = _TAG.sub(" ", body)
    body = _htmllib.unescape(body)

    lines = [" ".join(line.split()) for line in body.splitlines()]
    text = "\n".join(line for line in lines if line)
    text = _MANY_BLANK.sub("\n\n", text).strip()
    return (title, text[:MAX_EXTRACT_CHARS])


# ── PDF (pure-Python, best-effort text layer) ───────────────────────────────
_PDF_STREAM = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.DOTALL)
_PDF_INFO_TITLE = re.compile(rb"/Title\s*\(((?:\\.|[^\\)])*)\)", re.DOTALL)
# Text-showing operators inside a content stream: (str)Tj  and  [..]TJ
_PDF_TJ = re.compile(rb"\((?:\\.|[^\\)])*\)\s*Tj", re.DOTALL)
_PDF_TJ_ARRAY = re.compile(rb"\[(.*?)\]\s*TJ", re.DOTALL)
_PDF_STRING = re.compile(rb"\((?:\\.|[^\\)])*\)", re.DOTALL)
# Positioning operators that imply a new line of text.
_PDF_LINE_BREAK = re.compile(rb"(?:T\*|'|\"|(?:-?\d[\d.]*\s+){2}Td|(?:-?\d[\d.]*\s+){2}TD)")
_PDF_OCTAL = re.compile(rb"\\([0-7]{1,3})")
_PDF_ESCAPES = {
    b"\\n": b"\n",
    b"\\r": b"\r",
    b"\\t": b"\t",
    b"\\b": b"\b",
    b"\\f": b"\f",
    b"\\(": b"(",
    b"\\)": b")",
    b"\\\\": b"\\",
}


def _decode_pdf_string(raw: bytes) -> str:
    """Decode one `(...)` PDF literal string, resolving escapes (PDF §7.3.4.2)."""
    inner = raw[1:-1]  # drop the surrounding parens
    for src, dst in _PDF_ESCAPES.items():
        inner = inner.replace(src, dst)
    inner = _PDF_OCTAL.sub(lambda m: bytes([int(m.group(1), 8) & 0xFF]), inner)
    # PDF text strings are PDFDocEncoding/Latin-1 for the common (non-CID) case.
    return inner.decode("latin-1", errors="replace")


def _text_from_content(stream: bytes) -> str:
    """Pull the visible text layer out of one decoded content stream."""
    out: list[str] = []
    # Walk operators in order so line breaks land between the right runs.
    pos = 0
    for token in re.finditer(rb"(\((?:\\.|[^\\)])*\)\s*Tj)|(\[.*?\]\s*TJ)|(T\*|'|\")", stream, re.DOTALL):
        chunk = token.group(0)
        if token.group(1):  # (str) Tj
            s = _PDF_STRING.search(chunk)
            if s:
                out.append(_decode_pdf_string(s.group(0)))
        elif token.group(2):  # [ (a) n (b) ] TJ
            parts = [_decode_pdf_string(m.group(0)) for m in _PDF_STRING.finditer(chunk)]
            out.append("".join(parts))
        else:  # line-break operator
            out.append("\n")
        pos = token.end()
    _ = pos
    return "".join(out)


def extract_pdf_text(data: bytes) -> tuple[str, str]:
    """Best-effort PDF → (title, text). Never raises.

    Handles the common text-based case: FlateDecode-compressed or raw content
    streams with standard/WinAnsi text encoding. Scanned image PDFs and custom
    CID-font encodings yield little or nothing — the caller then honestly reports
    "no extractable text" rather than indexing noise (§22).
    """
    try:
        title = ""
        tm = _PDF_INFO_TITLE.search(data)
        if tm:
            title = _decode_pdf_string(b"(" + tm.group(1) + b")").strip()

        pieces: list[str] = []
        total = 0
        for sm in _PDF_STREAM.finditer(data):
            raw = sm.group(1)
            try:
                content = zlib.decompress(raw)
            except zlib.error:
                content = raw  # uncompressed stream, or a filter we don't handle
            # A content stream with text operators is what we want; skip binary blobs.
            if b"Tj" not in content and b"TJ" not in content:
                continue
            piece = _text_from_content(content)
            if piece.strip():
                pieces.append(piece)
                total += len(piece)
            if total > MAX_EXTRACT_CHARS:
                break

        text = "\n".join(pieces)
        # Collapse the ragged whitespace PDFs love, keep paragraph breaks.
        text = re.sub(r"[ \t]+", " ", text)
        text = _MANY_BLANK.sub("\n\n", text).strip()
        return (title, text[:MAX_EXTRACT_CHARS])
    except Exception:
        return ("", "")


def looks_like_pdf(content_type: str, body: bytes) -> bool:
    """PDF by declared type or magic bytes — magic wins over a lying header."""
    if body[:5] == b"%PDF-":
        return True
    return content_type in ("application/pdf", "application/x-pdf")
