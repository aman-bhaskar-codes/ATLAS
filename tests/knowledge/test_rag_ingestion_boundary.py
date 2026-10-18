"""Uploads must enter the same corpus that research retrieves and reranks."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile

from atlas.interfaces.api.routes_knowledge import (
    IngestRequest,
    SearchRequest,
    ingest_document,
    ingest_upload,
    search_knowledge,
)
from atlas.knowledge.domain import SourceType
from atlas.knowledge.ingestion import IngestionPipeline
from atlas.knowledge.research import ResearchRunner
from atlas.knowledge.retrieval import HybridRetriever
from atlas.knowledge.store import FabricStore
from tests.fakes import FakeClock, FakeIdGen


def _atlas(
    pipeline: IngestionPipeline, retriever: HybridRetriever, store: FabricStore, read_paths: list[str]
) -> object:
    return SimpleNamespace(
        manifest=SimpleNamespace(allowed_paths={"read": read_paths}, safety={"credential_dirs": []}),
        knowledge_fabric=SimpleNamespace(pipeline=pipeline, retriever=retriever, store=store),
    )


async def test_api_path_ingestion_is_searchable_by_shared_retriever(
    pipeline: IngestionPipeline, retriever: HybridRetriever, store: FabricStore, tmp_path: Path
) -> None:
    path = tmp_path / "report.txt"
    path.write_text("Orchid turbine produces 275 megawatts.")
    atlas = _atlas(pipeline, retriever, store, [str(tmp_path / "*")])
    response = await ingest_document(IngestRequest(source_path=str(path), source_type="txt"), atlas)  # type: ignore[arg-type]
    search = await search_knowledge(SearchRequest(query="Orchid turbine"), atlas)  # type: ignore[arg-type]
    assert response.indexed and response.chunks == 1
    assert search.results[0].document_id == response.document_id
    assert search.results[0].source_path == str(path)


async def test_api_denies_path_outside_manifest_before_parsing(
    pipeline: IngestionPipeline, retriever: HybridRetriever, store: FabricStore, tmp_path: Path
) -> None:
    path = tmp_path / "secret.txt"
    path.write_text("not authorized")
    atlas = _atlas(pipeline, retriever, store, [str(tmp_path / "allowed" / "*")])
    with pytest.raises(HTTPException) as exc:
        await ingest_document(IngestRequest(source_path=str(path), source_type="txt"), atlas)  # type: ignore[arg-type]
    assert exc.value.status_code == 403
    assert await store.list_documents() == []


async def test_api_upload_retains_original_name_and_reaches_research_corpus(
    pipeline: IngestionPipeline, retriever: HybridRetriever, store: FabricStore
) -> None:
    pytest.importorskip("pypdf", reason="optional PDF support requires uv sync --extra ocr")
    atlas = _atlas(pipeline, retriever, store, [])
    fixture = Path(__file__).parent / "fixtures" / "orchid_text.pdf"
    with fixture.open("rb") as handle:
        upload = UploadFile(filename="original-report.pdf", file=handle)
        response = await ingest_upload(upload, atlas, source_type="pdf")  # type: ignore[arg-type]
    result = await retriever.retrieve("Helium annual inspection")
    assert result.candidates[0].document.document_id == response.document_id
    assert result.candidates[0].document.uri == "upload://original-report.pdf"
    assert "tmp" not in result.candidates[0].document.uri


async def test_research_reranks_authoritative_sources_before_selecting(
    pipeline: IngestionPipeline, retriever: HybridRetriever, store: FabricStore, ids: FakeIdGen, clock: FakeClock
) -> None:
    await pipeline.ingest(
        source_id="blog",
        source_type=SourceType.WEB_PAGE,
        uri="https://blog.test/turbine",
        content="Orchid turbine output is 100 megawatts.",
        authority=0.1,
    )
    trusted = await pipeline.ingest(
        source_id="official",
        source_type=SourceType.WEB_PAGE,
        uri="https://official.test/turbine",
        content="Orchid turbine output is 275 megawatts.",
        authority=1.0,
    )
    outcome = await ResearchRunner(retriever, store, ids, clock).start("Orchid turbine output", resume=False)
    assert outcome.candidates[0].document.document_id == trusted.document_id


async def test_identical_private_and_web_content_keep_separate_source_provenance(
    pipeline: IngestionPipeline, store: FabricStore
) -> None:
    text = "Orchid turbine produces 275 megawatts."
    private = await pipeline.ingest(source_id="private.pdf", source_type=SourceType.LOCAL_FILE, content=text)
    web = await pipeline.ingest(source_id="web", source_type=SourceType.WEB_PAGE, content=text)
    assert private.document_id != web.document_id
    documents = await store.list_documents()
    assert {d.source_type for d in documents} == {SourceType.LOCAL_FILE, SourceType.WEB_PAGE}


async def test_same_corpus_reingestion_retains_source_aliases_without_duplicate_chunks(
    pipeline: IngestionPipeline, store: FabricStore
) -> None:
    text = "Orchid turbine produces 275 megawatts."
    first = await pipeline.ingest(
        source_id="first", source_type=SourceType.WEB_PAGE, uri="https://first.test/report", content=text
    )
    again = await pipeline.ingest(
        source_id="second", source_type=SourceType.WEB_PAGE, uri="https://second.test/report", content=text
    )
    assert again.document_id == first.document_id
    assert len(await store.all_chunks()) == 1
    doc = await store.get_document(first.document_id or "")
    assert doc is not None
    assert doc.provenance["source_aliases"] == ["https://first.test/report", "https://second.test/report"]


async def test_ingestion_updates_stale_pipeline_version(pipeline: IngestionPipeline, store: FabricStore) -> None:
    text = "Orchid turbine produces 275 megawatts."
    first = await pipeline.ingest(source_id="a", source_type=SourceType.WEB_PAGE, content=text)
    doc = await store.get_document(first.document_id or "")
    assert doc is not None
    chunks = [c for c, _ in await store.all_chunks()]
    await store.save_document(doc.model_copy(update={"pipeline_version": "obsolete"}), chunks)
    again = await pipeline.ingest(source_id="a", source_type=SourceType.WEB_PAGE, content=text)
    refreshed = await store.get_document(again.document_id or "")
    assert refreshed is not None and refreshed.pipeline_version != "obsolete"


@pytest.mark.parametrize("escape", ["traversal", "symlink", "credential"])
async def test_api_rejects_credential_and_path_escape(
    pipeline: IngestionPipeline, retriever: HybridRetriever, store: FabricStore, tmp_path: Path, escape: str
) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    denied = tmp_path / "private"
    denied.mkdir()
    secret = denied / "credentials.txt"
    secret.write_text("not corpus material")
    atlas = _atlas(pipeline, retriever, store, [str(allowed / "*")])
    path = str(secret)
    if escape == "traversal":
        path = str(allowed / ".." / "private" / "credentials.txt")
    elif escape == "symlink":
        link = allowed / "link.txt"
        link.symlink_to(secret)
        path = str(link)
    else:
        atlas.manifest.allowed_paths["read"] = [str(tmp_path / "*")]  # type: ignore[attr-defined]
        atlas.manifest.safety["credential_dirs"] = [str(denied)]  # type: ignore[attr-defined]
    with pytest.raises(HTTPException) as exc:
        await ingest_document(IngestRequest(source_path=path, source_type="txt"), atlas)  # type: ignore[arg-type]
    assert exc.value.status_code == 403
    assert not await store.all_chunks()


async def test_api_rejects_oversized_upload_without_indexing(
    pipeline: IngestionPipeline, retriever: HybridRetriever, store: FabricStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io

    monkeypatch.setattr("atlas.interfaces.api.routes_knowledge.MAX_DOCUMENT_BYTES", 100)
    upload = UploadFile(filename="oversize.txt", file=io.BytesIO(b"x" * 101))
    with pytest.raises(HTTPException) as exc:
        await ingest_upload(upload, _atlas(pipeline, retriever, store, []))  # type: ignore[arg-type]
    assert exc.value.status_code == 413
    assert not await store.all_chunks()


async def test_scanned_page_location_survives_research_payload_and_persistence(
    fabric: object, pipeline: IngestionPipeline
) -> None:
    pytest.importorskip("pypdf", reason="optional PDF support requires uv sync --extra ocr")
    from atlas.knowledge.domain import RAGMode
    from atlas.orchestration.research.records import answer_from_payload
    from atlas.tools.research import _answer_payload

    engine, _ = fabric  # type: ignore[misc]
    await pipeline.ingest_file(Path(__file__).parent / "fixtures" / "orchid_text.pdf")
    answer = await engine.query("Helium engine annual inspection", mode=RAGMode.RAG)
    persisted = answer_from_payload(_answer_payload(answer))
    assert persisted.citations[0].location == "page 2"
    assert "Helium engine" in persisted.citations[0].quote
    restored = type(persisted).model_validate_json(persisted.model_dump_json())
    assert restored.citations[0].location == "page 2"


async def test_source_aliases_do_not_retain_url_credentials_or_tracking_tokens(
    pipeline: IngestionPipeline, store: FabricStore
) -> None:
    job = await pipeline.ingest(
        source_id="public",
        source_type=SourceType.WEB_PAGE,
        uri="https://user:password@example.test/report?api_key=secret#fragment",
        content="Orchid turbine output.",
    )
    doc = await store.get_document(job.document_id or "")
    assert doc is not None
    assert doc.provenance["source_aliases"] == ["https://example.test/report"]
