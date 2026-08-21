"""CodebaseKnowledge + citation-preserving compression tests (§48-49, §122-123)."""

from __future__ import annotations

from pathlib import Path

from atlas.knowledge.codebase import CodebaseKnowledge
from atlas.knowledge.compression import CitationPreservingCompressor
from atlas.knowledge.domain import IngestionState, SourceType
from atlas.knowledge.ingestion import IngestionPipeline
from atlas.knowledge.retrieval import HybridRetriever
from atlas.knowledge.store import FabricStore


def _make_repo(root: Path) -> None:
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("def main():\n    return 'atlas'\n", encoding="utf-8")
    (root / "README.md").write_text("# Repo\n\nA tiny repository used for fabric indexing tests.", encoding="utf-8")
    (root / "notes.txt").write_text("Plain notes about the repository layout and purpose.", encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "skip.js").write_text("console.log('never indexed');", encoding="utf-8")
    (root / "image.bin").write_bytes(b"\x00\x01")  # non-indexed suffix
    (root / "big.py").write_text("x = 1\n" * 100_000, encoding="utf-8")  # > 200KB cap


async def test_ingest_repo_indexes_supported_files_only(
    pipeline: IngestionPipeline, store: FabricStore, retriever: HybridRetriever, tmp_path: Path
) -> None:
    _make_repo(tmp_path)
    jobs = await CodebaseKnowledge(pipeline).ingest_repo(tmp_path)
    ready = [j for j in jobs if j.state is IngestionState.READY]
    assert len(ready) == 3  # app.py, README.md, notes.txt (skip.js, image.bin, big.py excluded)

    docs = await store.list_documents()
    assert all(d.source_type is SourceType.LOCAL_FILE for d in docs)
    uris = {d.uri for d in docs}
    assert not any("node_modules" in u for u in uris)
    assert not any("big.py" in u for u in uris)

    # codebase content is retrievable through the shared index
    found = await retriever.retrieve("repository layout purpose")
    assert found.candidates


async def test_ingest_repo_respects_max_files(pipeline: IngestionPipeline, tmp_path: Path) -> None:
    for i in range(5):
        (tmp_path / f"f{i}.txt").write_text(f"document number {i} with enough words to index.", encoding="utf-8")
    jobs = await CodebaseKnowledge(pipeline, max_files=2).ingest_repo(tmp_path)
    assert len(jobs) == 2


async def test_ingest_repo_empty_directory(pipeline: IngestionPipeline, tmp_path: Path) -> None:
    assert await CodebaseKnowledge(pipeline).ingest_repo(tmp_path) == []


async def test_reingest_repo_is_cheap_via_dedupe(
    pipeline: IngestionPipeline, store: FabricStore, tmp_path: Path
) -> None:
    (tmp_path / "a.md").write_text("# A\n\nContent about steam engines and their history.", encoding="utf-8")
    kb = CodebaseKnowledge(pipeline)
    await kb.ingest_repo(tmp_path)
    await kb.ingest_repo(tmp_path)  # unchanged content (§24)
    assert len(await store.list_documents()) == 1


# ── CitationPreservingCompressor ────────────────────────────────────────
def test_compressor_keeps_cited_sentences_and_markers() -> None:
    text = (
        "Steam engines dominated early industry [1]. "
        "An uncited aside about the weather today. "
        "Watt's condenser doubled efficiency [2]. "
        "Another uncited opinion about engines overall."
    )
    out = CitationPreservingCompressor().compress(text, max_chars=120, query="steam engines efficiency")
    assert "[1]" in out.text and "[2]" in out.text
    assert "uncited aside" not in out.text
    assert out.kept_markers == (1, 2)
    assert out.provenance_complete is True
    assert out.kept_sentences <= out.total_sentences


def test_compressor_restores_original_sentence_order() -> None:
    text = "First fact [1]. Second fact [2]. Third fact [3]."
    out = CitationPreservingCompressor().compress(text, max_chars=500)
    assert out.text.index("First") < out.text.index("Second") < out.text.index("Third")


def test_compressor_flags_incomplete_provenance() -> None:
    text = "Cited fact [1]. An uncited sentence that will be kept because the budget is huge."
    out = CitationPreservingCompressor().compress(text, max_chars=10_000)
    assert out.provenance_complete is False  # must NOT enter trusted semantic memory


def test_compressor_empty_input_is_safe() -> None:
    out = CitationPreservingCompressor().compress("")
    assert out.text == ""
    assert out.kept_sentences == 0
    assert out.provenance_complete is True


def test_compressor_query_overlap_breaks_ties_between_cited_sentences() -> None:
    text = "Engines and boilers [1]. Baking bread slowly [2]. Engines powering factories [3]."
    out = CitationPreservingCompressor().compress(text, max_chars=60, query="engines")
    assert "[1]" in out.text or "[3]" in out.text
    assert "[2]" not in out.text  # least relevant cited sentence dropped first
