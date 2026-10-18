"""One-shot parser/OCR subprocess. All inputs and native tool work stay local."""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import warnings
from pathlib import Path
from typing import Any

SETUP = "Install Python support with `uv sync --extra ocr`; install Tesseract and Poppler for scanned documents."


def _limit_resources(options: dict[str, Any]) -> None:
    if os.name != "posix":
        return
    import resource

    # Darwin does not reliably implement RLIMIT_AS. CPU/file limits still apply.
    if sys.platform != "darwin":
        resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
    cpu = math.ceil(options["timeout_s"]) + 1
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024 * 1024, 64 * 1024 * 1024))


def _runtime(name: str, options: dict[str, Any]) -> str:
    executable = str(options[name])
    if not executable or not shutil.which(executable):
        raise ValueError(f"{name} runtime unavailable. {SETUP}")
    return executable


def _ocr_image(path: Path, work: Path, options: dict[str, Any]) -> str:
    try:
        from PIL import Image
    except ImportError as exc:
        raise ValueError(f"Pillow unavailable. {SETUP}") from exc
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                if image.width * image.height > options["max_pixels"]:
                    raise ValueError("image exceeds pixel limit")
                image.load()
                normalized = work / "normalized.png"
                image.convert("RGB").save(normalized)
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("image exceeds pixel limit") from exc
    except (OSError, SyntaxError) as exc:
        raise ValueError("invalid image document") from exc
    output = work / "ocr"
    subprocess.run(
        [_runtime("tesseract", options), str(normalized), str(output), "-l", options["language"], "--psm", "6"],
        check=True,
        timeout=options["timeout_s"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    with output.with_suffix(".txt").open("rb") as handle:
        data = handle.read(options["max_chars"] * 4 + 1)
    if len(data) > options["max_chars"] * 4:
        raise ValueError("document exceeds extraction output limit")
    return data.decode("utf-8").strip()


def extract(path: Path, options: dict[str, Any]) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size > options["max_bytes"]:
        raise ValueError("document exceeds file size limit or is not a file")
    pages: list[tuple[int, str, str]] = []
    with tempfile.TemporaryDirectory(prefix="atlas-ocr-") as tmp:
        work = Path(tmp)
        if options["kind"] == "pdf":
            try:
                from pypdf import PdfReader, overwrite_configuration
                from pypdf.errors import LimitReachedError
            except ImportError as exc:
                raise ValueError(f"PDF parser unavailable. {SETUP}") from exc
            with path.open("rb") as handle:
                if handle.read(5) != b"%PDF-":
                    raise ValueError("invalid PDF document")
            decode_limit = max(1_000_000, min(options["max_bytes"], options["max_chars"] * 8))
            overwrite_configuration(
                maximum_declared_stream_length=options["max_bytes"],
                array_based_stream_maximum_output_length=decode_limit,
                zlib_maximum_output_length=decode_limit,
                lzw_maximum_output_length=decode_limit,
                run_length_maximum_output_length=decode_limit,
                jbig2_maximum_output_length=decode_limit,
                page_tree_maximum_entries=options["max_pages"] * 4 + 20,
                xform_maximum_invocations_per_extraction=500,
            )
            reader = PdfReader(path, strict=True)
            if reader.is_encrypted:
                raise ValueError("encrypted PDF unsupported; decrypt locally first")
            if len(reader.pages) > options["max_pages"]:
                raise ValueError("PDF exceeds page limit")
            for index, page in enumerate(reader.pages, 1):
                try:
                    contents = page.get_contents()
                    if contents is not None and len(contents.get_data()) > decode_limit:
                        raise ValueError("PDF exceeds decode limit")
                    text = (page.extract_text() or "").strip()
                except LimitReachedError as exc:
                    raise ValueError("PDF exceeds decode limit") from exc
                method = "pdf_text"
                if not text:
                    prefix = work / "page"
                    subprocess.run(
                        [
                            _runtime("pdftoppm", options),
                            "-f",
                            str(index),
                            "-l",
                            str(index),
                            "-singlefile",
                            "-scale-to",
                            "2400",
                            "-png",
                            str(path),
                            str(prefix),
                        ],
                        check=True,
                        timeout=options["timeout_s"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    text, method = _ocr_image(prefix.with_suffix(".png"), work, options), "tesseract"
                pages.append((index, text, method))
                if sum(len(p[1]) + 2 for p in pages) > options["max_chars"]:
                    raise ValueError("document exceeds extraction output limit")
        else:
            # Require one frame: silently dropping pages of a TIFF/GIF is unsafe.
            try:
                from PIL import Image
            except ImportError as exc:
                raise ValueError(f"Pillow unavailable. {SETUP}") from exc
            try:
                with Image.open(path) as image:
                    if getattr(image, "n_frames", 1) != 1:
                        raise ValueError("multi-frame image unsupported; convert to PDF or individual pages")
            except OSError as exc:
                raise ValueError("invalid image document") from exc
            pages.append((1, _ocr_image(path, work, options), "tesseract"))
    text = ""
    spans: list[dict[str, Any]] = []
    for number, body, method in pages:
        start = len(text)
        text += body
        spans.append({"page": number, "start": start, "end": len(text), "method": method})
        text += "\n\n"
    text = text.rstrip()
    if not text:
        raise ValueError("document extraction produced no usable text")
    if len(text) > options["max_chars"]:
        raise ValueError("document exceeds extraction output limit")
    return {"text": text, "pages": spans}


def main() -> None:
    try:
        options = json.loads(sys.argv[2])
        _limit_resources(options)
        result = extract(Path(sys.argv[1]), options)
    except subprocess.TimeoutExpired:
        result = {"error": "document extraction timed out"}
    except (ValueError, OSError, ImportError, MemoryError, subprocess.CalledProcessError) as exc:
        result = {"error": str(exc)[:500] or "document extraction failed"}
    except Exception:
        result = {"error": "invalid or unsupported document"}
    sys.stdout.write(json.dumps(result))


if __name__ == "__main__":
    main()
