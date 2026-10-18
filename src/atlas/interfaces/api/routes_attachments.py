"""Bounded local attachment text store; uploaded document contents are untrusted data."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from atlas.interfaces.api.schemas import AttachmentRef
from atlas.knowledge.ocr import DocumentExtractionError, LocalDocumentExtractor
from atlas.tools.extract import extract_pdf_text

router = APIRouter(tags=["attachments"])
ATTACHMENT_DIR = os.path.expanduser(os.path.join(os.environ.get("ATLAS_DATA_DIR", "./.atlas"), "attachments"))
MAX_UPLOAD_BYTES = 65_536
MAX_STORED_ATTACHMENTS = 128
MAX_STORE_BYTES = 1_048_576
RETENTION_SECONDS = 86_400
_STORE_LOCK = threading.Lock()
_ID = re.compile(r"att_[0-9a-f]{32}")
_TEXT_SUFFIXES = {
    ".txt",
    ".md",
    ".markdown",
    ".json",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".go",
    ".rs",
    ".cpp",
    ".c",
    ".h",
    ".css",
    ".html",
    ".yaml",
    ".yml",
    ".sh",
    ".sql",
}


def _prune(directory: Path) -> list[Path]:
    files = []
    cutoff = time.time() - RETENTION_SECONDS
    for path in directory.iterdir():
        if not _ID.fullmatch(path.stem) or path.suffix not in {".txt", ".json"}:
            continue
        if path.is_symlink() or path.stat().st_mtime < cutoff:
            path.unlink(missing_ok=True)
        else:
            files.append(path)
    return files


def resolve_attachment(ref: AttachmentRef) -> str:
    """Resolve an opaque ref, validate its declared type, and read retained text."""
    if not _ID.fullmatch(ref.id):
        raise HTTPException(404, "Attachment not found; attach the file again.")
    directory = Path(ATTACHMENT_DIR)
    with _STORE_LOCK:
        try:
            if not directory.exists():
                raise FileNotFoundError
            _prune(directory)
            text_path = directory / f"{ref.id}.txt"
            meta_path = directory / f"{ref.id}.json"
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
            if metadata["type"] != ref.type:
                raise HTTPException(422, "Attachment type does not match its upload.")
            if text_path.stat().st_size > MAX_UPLOAD_BYTES:
                raise HTTPException(413, "Attachment text exceeds the size limit.")
            return text_path.read_text(encoding="utf-8")
        except (OSError, ValueError, KeyError) as exc:
            raise HTTPException(404, "Attachment not found; attach the file again.") from exc


@router.post("/attachments", response_model=AttachmentRef)
async def upload_attachment(file: UploadFile = File(...)) -> AttachmentRef:
    """Accept bounded text/PDF uploads and locally extract image text with OCR."""
    try:
        data = await file.read(MAX_UPLOAD_BYTES + 1)
    finally:
        await file.close()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Files must be at most 64 KiB.")
    suffix = Path(file.filename or "").suffix.lower()
    mime = (file.content_type or "").split(";")[0].lower()
    att_type: Literal["image", "pdf", "markdown", "code"]
    if mime.startswith("image/") or suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        # Never retain the original image or run a cloud vision provider implicitly.
        # The worker validates the format/pixel count and bounds OCR subprocesses.
        with tempfile.TemporaryDirectory(prefix="atlas-attachment-") as directory:
            path = Path(directory) / "upload.png"
            await run_in_threadpool(path.write_bytes, data)
            try:
                parsed = await LocalDocumentExtractor(max_bytes=MAX_UPLOAD_BYTES, timeout_s=5.0).extract(path)
            except DocumentExtractionError as exc:
                raise HTTPException(415, f"Image text extraction failed: {exc}") from exc
        text = "[Locally extracted image text; visual details are not included]\n" + parsed.text
        att_type = "image"
    elif suffix == ".pdf" or mime == "application/pdf" or data.startswith(b"%PDF-"):
        if not data.startswith(b"%PDF-") or re.search(rb"/Encrypt\b", data):
            raise HTTPException(415, "Invalid or encrypted PDF. Attach an unencrypted text-based PDF.")
        _, text = extract_pdf_text(data)
        att_type = "pdf"
        if not text.strip():
            raise HTTPException(415, "PDF has no supported text layer; scanned PDFs require text extraction first.")
    elif suffix in _TEXT_SUFFIXES or mime in {"text/plain", "text/markdown", "application/json"}:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(415, "Text attachments must use UTF-8 encoding.") from exc
        if any(ord(char) < 32 and char not in "\n\r\t" for char in text):
            raise HTTPException(415, "Binary content is not supported in text attachments.")
        att_type = "markdown" if suffix in {".md", ".markdown"} or mime == "text/markdown" else "code"
    else:
        raise HTTPException(415, "Unsupported file. Attach UTF-8 text, code, markdown, JSON, or a text-based PDF.")
    encoded = text.encode("utf-8")
    if not text.strip():
        raise HTTPException(415, "The attachment contains no readable text.")
    if len(encoded) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Extracted attachment text exceeds 64 KiB.")
    return await run_in_threadpool(_store_attachment, encoded, att_type)


def _store_attachment(encoded: bytes, att_type: Literal["image", "pdf", "markdown", "code"]) -> AttachmentRef:
    att_id = f"att_{uuid.uuid4().hex}"
    metadata = json.dumps({"type": att_type}).encode("utf-8")
    directory = Path(ATTACHMENT_DIR)
    with _STORE_LOCK:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        files = _prune(directory)
        if (
            len([p for p in files if p.suffix == ".txt"]) >= MAX_STORED_ATTACHMENTS
            or sum(p.stat().st_size for p in files) + len(encoded) + len(metadata) > MAX_STORE_BYTES
        ):
            raise HTTPException(413, "Attachment storage is full. Uploads expire after 24 hours.")
        written: list[Path] = []
        try:
            for suffix, payload in ((".txt", encoded), (".json", metadata)):
                path = directory / f"{att_id}{suffix}"
                with path.open("xb") as buffer:
                    written.append(path)
                    os.chmod(path, 0o600)
                    buffer.write(payload)
        except OSError as exc:
            for path in written:
                path.unlink(missing_ok=True)
            raise HTTPException(503, "Attachment storage is unavailable. Try again.") from exc
    return AttachmentRef(id=att_id, type=att_type)
