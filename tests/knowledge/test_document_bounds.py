"""Chunk windows must stay bounded and progress even without punctuation."""

from __future__ import annotations

import pytest

from atlas.knowledge.chunking import chunk_parsed
from atlas.knowledge.document_processor import DocumentProcessor
from atlas.knowledge.parsers import parse_text
from tests.knowledge.harness import FakeEmbedder, FakeVector


def test_shared_chunker_bounds_unpunctuated_units_and_preserves_offsets() -> None:
    text = "ABCDEFGHIJ" * 150
    chunks = chunk_parsed(parse_text(text), "long", max_chars=100, overlap=15)
    assert len(chunks) > 1
    assert all(len(c.content) <= 100 for c in chunks)
    assert all(text[c.char_start : c.char_end] == c.content for c in chunks)
    assert chunks[-1].char_end == len(text)


def test_document_processor_zero_overlap_does_not_repeat_previous_chunks() -> None:
    processor = DocumentProcessor(FakeVector(), FakeEmbedder(), chunk_size=100, chunk_overlap=0)
    chunks = processor._chunk_text("a" * 80 + "\n\n" + "b" * 80 + "\n\n" + "c" * 80, "notes")
    assert [c.text for c in chunks] == ["a" * 80, "b" * 80, "c" * 80]


def test_document_processor_splits_large_paragraph() -> None:
    processor = DocumentProcessor(FakeVector(), FakeEmbedder(), chunk_size=100, chunk_overlap=10)
    chunks = processor._chunk_text("x" * 500, "notes")
    assert len(chunks) > 1
    assert all(len(c.text) <= 100 for c in chunks)


@pytest.mark.parametrize("chunk_size,overlap", [(0, 0), (100, 100), (100, -1)])
def test_invalid_chunk_windows_fail_immediately(chunk_size: int, overlap: int) -> None:
    with pytest.raises(ValueError):
        DocumentProcessor(FakeVector(), FakeEmbedder(), chunk_size=chunk_size, chunk_overlap=overlap)
    with pytest.raises(ValueError):
        chunk_parsed(parse_text("data"), "doc", max_chars=chunk_size, overlap=overlap)
