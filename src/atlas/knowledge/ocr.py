"""Local document extraction, isolated from the event loop and bounded by limits.

Optional Python libraries: ``uv sync --extra ocr``. Scanned PDFs additionally
need Poppler (pdftoppm) and Tesseract with the requested language installed.
No cloud API, model download, or fabricated text fallback is used.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import sys
from pathlib import Path
from typing import Any

from atlas.knowledge.parsers import MAX_PARSED_CHARS, PageSpan, Parsed, parse_content

MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
BINARY_EXTENSIONS = frozenset({".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".tif", ".tiff", ".bmp"})


class DocumentExtractionError(ValueError):
    """Unsupported, unavailable, malformed, empty, or over-budget extraction."""


class LocalDocumentExtractor:
    def __init__(
        self,
        *,
        tesseract: str | None = None,
        pdftoppm: str | None = None,
        max_bytes: int = MAX_DOCUMENT_BYTES,
        max_pages: int = 100,
        max_pixels: int = 20_000_000,
        max_chars: int = MAX_PARSED_CHARS,
        timeout_s: float = 60.0,
        language: str = "eng",
    ) -> None:
        if min(max_bytes, max_pages, max_pixels, max_chars) <= 0 or timeout_s <= 0:
            raise ValueError("extraction limits must be positive")
        self.max_bytes = max_bytes
        self._timeout = timeout_s
        self._max_chars = max_chars
        self._options = {
            "tesseract": tesseract or shutil.which("tesseract") or "",
            "pdftoppm": pdftoppm or shutil.which("pdftoppm") or "",
            "max_bytes": max_bytes,
            "max_pages": max_pages,
            "max_pixels": max_pixels,
            "max_chars": max_chars,
            "timeout_s": timeout_s,
            "language": language,
        }

    async def extract(self, path: Path) -> Parsed:
        # Read one byte beyond the limit; stat alone is subject to file changes.
        try:
            data = await asyncio.to_thread(_read_bounded, path, self.max_bytes)
        except OSError as exc:
            raise DocumentExtractionError("unreadable document") from exc
        if len(data) > self.max_bytes:
            raise DocumentExtractionError("document exceeds file size limit")
        if not data:
            raise DocumentExtractionError("empty document")
        suffix = path.suffix.lower()
        kind = "pdf" if data.startswith(b"%PDF-") or suffix == ".pdf" else ""
        image_magic = data.startswith((b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"II*\x00", b"MM\x00*", b"BM"))
        image_magic = image_magic or (data.startswith(b"RIFF") and data[8:12] == b"WEBP")
        if image_magic or (suffix in BINARY_EXTENSIONS and suffix != ".pdf"):
            kind = "image"
        if not kind:
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise DocumentExtractionError("invalid text encoding or unsupported binary document") from exc
            if any(ord(char) < 32 and char not in "\t\n\r" for char in text):
                raise DocumentExtractionError("unsupported binary document")
            if len(text) > self._max_chars:
                raise DocumentExtractionError("document exceeds extraction output limit")
            return parse_content(text, uri=str(path), title=path.name)
        return await self._extract_binary(path, kind=kind)

    async def _extract_binary(self, path: Path, *, kind: str) -> Parsed:
        worker = str(Path(__file__).with_name("document_worker.py"))
        resolved = await asyncio.to_thread(path.resolve)
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            worker,
            str(resolved),
            json.dumps({**self._options, "kind": kind}),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=(os.name == "posix"),
        )
        try:
            async with asyncio.timeout(self._timeout):
                assert process.stdout is not None
                output = bytearray()
                while data := await process.stdout.read(16_384):
                    output.extend(data)
                    if len(output) > self._max_chars * 12 + 60_000:
                        raise DocumentExtractionError("document exceeds extraction output limit")
                await process.wait()
        except TimeoutError as exc:
            raise DocumentExtractionError("document extraction timed out") from exc
        finally:
            if process.returncode is None:
                # Kill the entire local worker group, including rasterizer/OCR.
                try:
                    if os.name == "posix":
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()
        try:
            payload = json.loads(output)
        except (ValueError, UnicodeDecodeError) as exc:
            raise DocumentExtractionError("document extraction worker failed") from exc
        if process.returncode or payload.get("error"):
            raise DocumentExtractionError(str(payload.get("error") or "document extraction worker failed")[:500])
        text = payload["text"]
        if not isinstance(text, str) or not text.strip() or len(text) > self._max_chars:
            raise DocumentExtractionError("empty or oversized extraction output")
        pages = tuple(PageSpan(**page) for page in payload["pages"])
        return Parsed(
            text=text,
            title=path.name,
            kind="document",
            pages=pages,
            metadata={"content_type": "application/pdf" if kind == "pdf" else "image"},
        )


def _read_bounded(path: Path, limit: int) -> bytes:
    with path.open("rb") as handle:
        return handle.read(limit + 1)


def page_provenance(provenance: dict[str, Any], start: int, end: int) -> dict[str, Any]:
    """Select only typed page fields; never copy arbitrary document metadata."""
    pages = [
        p
        for p in provenance.get("pages", [])
        if isinstance(p, dict)
        and isinstance(p.get("start"), int)
        and isinstance(p.get("end"), int)
        and p["start"] < end
        and p["end"] > start
        and isinstance(p.get("page"), int)
        and p.get("method") in {"pdf_text", "tesseract"}
    ]
    if not pages:
        return {}
    return {"page_start": pages[0]["page"], "page_end": pages[-1]["page"], "extraction_method": pages[0]["method"]}
