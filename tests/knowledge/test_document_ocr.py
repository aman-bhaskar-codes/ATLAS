"""Real local document extraction and its shared research-index boundary."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from atlas.knowledge.domain import IngestionState
from atlas.knowledge.evidence import EvidenceSelector
from atlas.knowledge.ingestion import IngestionPipeline
from atlas.knowledge.retrieval import HybridRetriever
from atlas.knowledge.store import FabricStore
from tests.fakes import FakeClock, FakeIdGen

pytest.importorskip("pypdf", reason="optional OCR Python libraries require uv sync --extra ocr")
pytest.importorskip("PIL", reason="optional OCR Python libraries require uv sync --extra ocr")

FIXTURES = Path(__file__).parent / "fixtures"


async def test_text_pdf_is_extracted_without_ocr_runtime() -> None:
    from atlas.knowledge.ocr import LocalDocumentExtractor

    extractor = LocalDocumentExtractor(tesseract="/missing/tesseract", pdftoppm="/missing/pdftoppm")
    parsed = await extractor.extract(FIXTURES / "orchid_text.pdf")
    assert "275 megawatts" in parsed.text
    assert len(parsed.pages) == 2
    assert [p.method for p in parsed.pages] == ["pdf_text", "pdf_text"]
    assert parsed.text[parsed.pages[1].start : parsed.pages[1].end].startswith("Helium engine")


@pytest.mark.skipif(not shutil.which("tesseract"), reason="optional local tesseract runtime absent")
async def test_real_image_ocr_extracts_observed_text() -> None:
    from atlas.knowledge.ocr import LocalDocumentExtractor

    parsed = await LocalDocumentExtractor().extract(FIXTURES / "orchid_scan.png")
    assert "Orchid turbine" in parsed.text
    assert "275 megawatts" in parsed.text
    assert len(parsed.pages) == 1
    assert parsed.pages[0].method == "tesseract"


@pytest.mark.skipif(not shutil.which("tesseract") or not shutil.which("pdftoppm"), reason="local OCR runtime absent")
async def test_real_scanned_pdf_is_retrievable_with_page_provenance(
    pipeline: IngestionPipeline, retriever: HybridRetriever, store: FabricStore, ids: FakeIdGen, clock: FakeClock
) -> None:
    job = await pipeline.ingest_file(FIXTURES / "orchid_scan.pdf")
    assert job.state is IngestionState.READY
    result = await retriever.retrieve("Orchid turbine megawatts")
    assert result.candidates
    assert "275 megawatts" in result.candidates[0].chunk.content
    evidence = EvidenceSelector(ids, clock).select("Orchid turbine", result.candidates)
    assert evidence[0].location == "page 1"
    assert evidence[0].provenance["extraction_method"] == "tesseract"
    assert evidence[0].uri.endswith("orchid_scan.pdf")
    persisted = await store.get_document(job.document_id or "")
    assert persisted is not None and persisted.provenance["pages"][0]["page"] == 1


async def test_missing_ocr_runtime_reports_setup_without_indexing(
    store: FabricStore, retriever: HybridRetriever, ids: FakeIdGen, clock: FakeClock
) -> None:
    from atlas.knowledge.ocr import LocalDocumentExtractor

    pipeline = IngestionPipeline(
        store, retriever, ids, clock, extractor=LocalDocumentExtractor(tesseract="/missing/tesseract")
    )
    job = await pipeline.ingest_file(FIXTURES / "orchid_scan.png")
    assert job.state is IngestionState.FAILED
    assert "tesseract" in job.error and "install" in job.error.lower()
    assert await store.all_chunks() == []


async def test_invalid_binary_never_enters_text_index(pipeline: IngestionPipeline, tmp_path: Path) -> None:
    path = tmp_path / "invalid.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\nmalformed")
    job = await pipeline.ingest_file(path)
    assert job.state is IngestionState.FAILED
    assert "invalid" in job.error.lower()


async def test_extraction_rejects_file_size_and_pixel_bombs(tmp_path: Path) -> None:
    from PIL import Image

    from atlas.knowledge.ocr import DocumentExtractionError, LocalDocumentExtractor

    with pytest.raises(DocumentExtractionError, match="size"):
        await LocalDocumentExtractor(max_bytes=20).extract(FIXTURES / "orchid_scan.png")
    path = tmp_path / "oversize.png"
    Image.new("RGB", (200, 200)).save(path)
    with pytest.raises(DocumentExtractionError, match="pixel"):
        await LocalDocumentExtractor(max_pixels=100).extract(path)


async def test_extraction_timeout_stops_worker(tmp_path: Path) -> None:
    from atlas.knowledge.ocr import DocumentExtractionError, LocalDocumentExtractor

    runtime = tmp_path / "slow-tesseract"
    runtime.write_text("#!/bin/sh\nsleep 30\n", encoding="utf-8")
    runtime.chmod(0o700)
    with pytest.raises(DocumentExtractionError, match="timed out"):
        await LocalDocumentExtractor(tesseract=str(runtime), timeout_s=0.5).extract(FIXTURES / "orchid_scan.png")


async def test_extraction_output_is_capped() -> None:
    from atlas.knowledge.ocr import DocumentExtractionError, LocalDocumentExtractor

    with pytest.raises(DocumentExtractionError, match="output"):
        await LocalDocumentExtractor(max_chars=10).extract(FIXTURES / "orchid_text.pdf")


async def test_pdf_decompression_is_bounded(tmp_path: Path) -> None:
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, NameObject

    from atlas.knowledge.ocr import DocumentExtractionError, LocalDocumentExtractor

    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=200)
    stream = DecodedStreamObject()
    stream.set_data(b" " * 5_000_000)
    page[NameObject("/Contents")] = writer._add_object(stream.flate_encode())
    path = tmp_path / "bomb.pdf"
    with path.open("wb") as handle:
        writer.write(handle)
    with pytest.raises(DocumentExtractionError, match="decode limit"):
        await LocalDocumentExtractor().extract(path)


@pytest.mark.skipif(not shutil.which("tesseract") or not shutil.which("pdftoppm"), reason="local OCR runtime absent")
async def test_scan_to_shared_rag_answer_renders_page_citation(fabric: object, pipeline: IngestionPipeline) -> None:
    from atlas.knowledge.domain import RAGMode

    engine, _telemetry = fabric  # type: ignore[misc]
    await pipeline.ingest_file(FIXTURES / "orchid_scan.pdf")
    answer = await engine.query("How many megawatts does Orchid turbine produce?", mode=RAGMode.RAG)
    assert answer.answered
    assert "275 megawatts" in answer.text
    assert answer.citations[0].location == "page 1"
    from atlas.knowledge.citations import CitationEngine

    assert "page 1" in CitationEngine().render_markdown(list(answer.citations))
    assert answer.evidence[0].provenance["extraction_method"] == "tesseract"


async def test_binary_signature_cannot_be_indexed_as_plain_text(pipeline: IngestionPipeline, tmp_path: Path) -> None:
    path = tmp_path / "disguised.txt"
    path.write_bytes(b"%PDF-1.7\ninvalid PDF without usable text")
    result = await pipeline.ingest_file(path)
    assert result.state is IngestionState.FAILED


async def test_nul_bytes_are_rejected_as_binary(pipeline: IngestionPipeline, tmp_path: Path) -> None:
    path = tmp_path / "data.txt"
    path.write_bytes(b"Orchid\x00turbine\x00raw binary")
    result = await pipeline.ingest_file(path)
    assert result.state is IngestionState.FAILED
