"""Extraction tests (§6, §7): structure-aware HTML + dependency-free PDF text.

Fakes only, no network. A hand-built PDF proves the text layer is really pulled
from `Tj`/`TJ` operators (compressed and raw), and the failure cases prove the
extractor DEGRADES to empty rather than raising or emitting garbage (§22).
"""

from __future__ import annotations

import zlib

from atlas.tools.extract import extract_pdf_text, html_to_text, looks_like_pdf


# ── HTML ────────────────────────────────────────────────────────────────
def test_html_extraction_drops_boilerplate_and_keeps_headings() -> None:
    title, text = html_to_text(
        "<html><head><title>Paper Title</title></head><body>"
        "<nav>Home About Contact</nav>"
        "<header>site banner</header>"
        "<h1>Main Heading</h1>"
        "<p>First paragraph of prose.</p>"
        "<h2>A Section</h2>"
        "<p>Second paragraph here.</p>"
        "<footer>copyright</footer>"
        "<script>evil()</script></body></html>"
    )
    assert title == "Paper Title"
    # boilerplate gone
    assert "Home About Contact" not in text
    assert "site banner" not in text
    assert "copyright" not in text
    assert "evil()" not in text
    # prose kept, headings preserved as markdown markers for the chunker
    assert "First paragraph of prose." in text
    assert "Second paragraph here." in text
    assert "# Main Heading" in text
    assert "## A Section" in text


def test_html_extraction_unescapes_entities() -> None:
    _title, text = html_to_text("<p>Tom &amp; Jerry &lt;3 fish&gt;</p>")
    assert "Tom & Jerry <3 fish>" in text


def test_html_extraction_falls_back_to_h1_when_no_title_tag() -> None:
    title, text = html_to_text("<body><h1>Fallback Title</h1><p>Body.</p></body>")
    assert title == "Fallback Title"
    assert "Body." in text


def test_html_title_hint_wins_over_document_title() -> None:
    title, _text = html_to_text("<title>Ignored</title><p>x</p>", title_hint="Given")
    assert title == "Given"


def test_html_extraction_tolerates_empty_input() -> None:
    assert html_to_text("") == ("", "")


# ── PDF ───────────────────────────────────────────────────────────────────
def _minimal_pdf(content_stream: bytes, *, compress: bool, title: str = "") -> bytes:
    """Assemble a tiny but structurally valid PDF around one content stream."""
    if compress:
        body = zlib.compress(content_stream)
        stream_obj = b"<< /Length %d /Filter /FlateDecode >>\nstream\n%s\nendstream" % (len(body), body)
    else:
        stream_obj = b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content_stream), content_stream)
    info = b""
    if title:
        info = b"5 0 obj\n<< /Title (%s) >>\nendobj\n" % title.encode("latin-1")
    return (
        b"%PDF-1.7\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /Contents 4 0 R >>\nendobj\n"
        b"4 0 obj\n" + stream_obj + b"\nendobj\n" + info + b"%%EOF"
    )


def test_pdf_extracts_text_from_a_flate_compressed_stream() -> None:
    content = b"BT /F1 12 Tf 72 720 Td (Autonomous adaptation matters.) Tj ET"
    title, text = extract_pdf_text(_minimal_pdf(content, compress=True, title="A Study"))
    assert title == "A Study"
    assert "Autonomous adaptation matters." in text


def test_pdf_extracts_text_from_an_uncompressed_stream() -> None:
    content = b"BT (Raw stream line one) Tj T* (line two) Tj ET"
    _title, text = extract_pdf_text(_minimal_pdf(content, compress=False))
    assert "Raw stream line one" in text
    assert "line two" in text


def test_pdf_reconstructs_tj_arrays_and_resolves_escapes() -> None:
    # A [ ... ] TJ array with kerning numbers, plus an escaped paren.
    content = b"BT [(Hello) -250 (World) -250 (\\(cited\\))] TJ ET"
    _title, text = extract_pdf_text(_minimal_pdf(content, compress=True))
    assert "HelloWorld(cited)" in text.replace(" ", "")


def test_pdf_without_a_text_layer_yields_empty() -> None:
    # A stream with no text operators (e.g. an image blob) → nothing to read.
    _title, text = extract_pdf_text(_minimal_pdf(b"q 100 0 0 100 0 0 cm /Im0 Do Q", compress=True))
    assert text == ""


def test_pdf_extractor_never_raises_on_garbage() -> None:
    assert extract_pdf_text(b"%PDF-1.7 not really a pdf at all") == ("", "")
    assert extract_pdf_text(b"") == ("", "")


def test_looks_like_pdf_trusts_magic_bytes_over_a_lying_header() -> None:
    assert looks_like_pdf("text/html", b"%PDF-1.5 ...") is True
    assert looks_like_pdf("application/pdf", b"anything") is True
    assert looks_like_pdf("text/html", b"<html>") is False
