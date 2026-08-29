"""LiveBridge tests (§7, §135): provider fan-out into the canonical pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from atlas.knowledge.domain import IngestionState, SourceType
from atlas.knowledge.ingestion import IngestionPipeline
from atlas.knowledge.providers_bridge import LiveBridge
from atlas.knowledge.store import FabricStore


@dataclass
class FakeItem:
    title: str
    snippet: str
    url: str | None = None
    published: object | None = None


@dataclass
class FakeScholarlyItem:
    """An item from a scholarly provider: same shape plus `citation_metadata()`.

    Mirrors `KnowledgeItem` structurally — the bridge reads the extra metadata
    via getattr precisely so legacy providers like `FakeItem` keep working.
    """

    title: str
    snippet: str
    url: str | None = None
    published: object | None = None
    meta: dict[str, str] | None = None

    def citation_metadata(self) -> dict[str, str]:
        return dict(self.meta or {})


class FakeProvider:
    def __init__(self, name: str, items: list[FakeItem] | None = None, *, fail: bool = False) -> None:
        self.name = name
        self.items = items or []
        self.fail = fail
        self.searched: list[str] = []

    async def search(self, query: str, *, limit: int) -> list[FakeItem]:
        self.searched.append(query)
        if self.fail:
            raise RuntimeError(f"{self.name} exploded")
        return self.items[:limit]


async def test_gather_ingests_provider_items_with_source_taxonomy(
    pipeline: IngestionPipeline, store: FabricStore
) -> None:
    arxiv = FakeProvider(
        "arxiv",
        [
            FakeItem(
                title="RRF Paper",
                snippet="Reciprocal rank fusion combines ranked lists robustly.",
                url="https://arxiv.org/abs/1",
            )
        ],
    )
    bridge = LiveBridge([arxiv], pipeline)
    jobs = await bridge.gather("rank fusion")
    assert len(jobs) == 1 and jobs[0].state is IngestionState.READY
    docs = await store.list_documents()
    assert docs[0].source_type is SourceType.ARXIV  # taxonomy mapping (§5)
    assert docs[0].provenance["pipe"] == "live"
    assert arxiv.searched == ["rank fusion"]


async def test_dead_provider_never_kills_the_fan_out(pipeline: IngestionPipeline, store: FabricStore) -> None:
    dead = FakeProvider("tavily", fail=True)
    alive = FakeProvider(
        "rss",
        [
            FakeItem(
                title="Feed Item",
                snippet="Steam engines powered the industrial revolution forward.",
                url="https://feed.test/1",
            )
        ],
    )
    bridge = LiveBridge([dead, alive], pipeline)
    jobs = await bridge.gather("steam engines")
    assert len(jobs) == 1 and jobs[0].state is IngestionState.READY
    docs = await store.list_documents()
    assert docs[0].source_type is SourceType.RSS


async def test_empty_snippets_are_skipped(pipeline: IngestionPipeline, store: FabricStore) -> None:
    provider = FakeProvider(
        "wikipedia",
        [
            FakeItem(title="Blank", snippet="   "),
            FakeItem(
                title="Real",
                snippet="Wikipedia describes the water cycle in detail here.",
                url="https://wikipedia.org/w",
            ),
        ],
    )
    bridge = LiveBridge([provider], pipeline)
    jobs = await bridge.gather("water cycle")
    assert len(jobs) == 1
    assert (await store.list_documents())[0].title == "Real"


async def test_repeat_fan_out_dedupes_on_content_hash(pipeline: IngestionPipeline, store: FabricStore) -> None:
    provider = FakeProvider(
        "arxiv",
        [
            FakeItem(
                title="P",
                snippet="Reciprocal rank fusion is robust to score scaling issues.",
                url="https://arxiv.org/abs/9",
            )
        ],
    )
    bridge = LiveBridge([provider], pipeline)
    await bridge.gather("rank fusion")
    jobs2 = await bridge.gather("rank fusion again")
    assert jobs2 and jobs2[0].state is IngestionState.READY  # deduped, not reprocessed
    assert len(await store.list_documents()) == 1


async def test_published_datetime_is_carried_through(pipeline: IngestionPipeline, store: FabricStore) -> None:
    when = datetime(2026, 8, 1, tzinfo=UTC)
    provider = FakeProvider(
        "github_releases",
        [
            FakeItem(
                title="v2.0",
                snippet="Release notes for version two of the library.",
                url="https://github.com/x/releases",
                published=when,
            )
        ],
    )
    bridge = LiveBridge([provider], pipeline)
    await bridge.gather("release notes")
    docs = await store.list_documents()
    assert docs[0].published_at == when
    assert docs[0].source_type is SourceType.GITHUB


async def test_non_datetime_published_is_ignored(pipeline: IngestionPipeline, store: FabricStore) -> None:
    provider = FakeProvider(
        "rss",
        [FakeItem(title="Odd", snippet="Some feed item with a strange published field.", published="yesterday")],
    )
    bridge = LiveBridge([provider], pipeline)
    jobs = await bridge.gather("feed")
    assert jobs and jobs[0].state is IngestionState.READY
    assert (await store.list_documents())[0].published_at is None


# ── scholarly metadata carry-through (§5, §9) ───────────────────────────
async def test_scholarly_metadata_reaches_the_document(pipeline: IngestionPipeline, store: FabricStore) -> None:
    provider = FakeProvider(
        "openalex",
        [
            FakeScholarlyItem(
                title="Autonomous Adaptation",
                snippet="Adaptive agents evaluate their own behaviour over time.",
                url="https://example.org/adapt",
                meta={
                    "authors": "Ada Lovelace; Alan Turing",
                    "doi": "10.1000/adapt",
                    "venue": "Journal of Adaptation",
                    "arxiv_id": "2602.00001",
                },
            )
        ],
    )
    await LiveBridge([provider], pipeline).gather("autonomous adaptation")
    doc = (await store.list_documents())[0]

    # scholarly index shares the 0.8 authority floor
    assert doc.source_type is SourceType.SEMANTIC_SCHOLAR
    assert doc.author == "Ada Lovelace; Alan Turing"
    assert doc.metadata["doi"] == "10.1000/adapt"
    assert doc.metadata["venue"] == "Journal of Adaptation"
    assert doc.metadata["provider"] == "openalex"
    # citation lines live IN the body so a bare chunk is still attributable
    assert "Authors: Ada Lovelace; Alan Turing" in doc.content
    assert "DOI: 10.1000/adapt" in doc.content
    assert "arXiv: 2602.00001" in doc.content


async def test_items_without_citation_metadata_still_ingest(pipeline: IngestionPipeline, store: FabricStore) -> None:
    # The nine legacy providers emit no scholarly fields; a missing DOI must
    # never cost us the document.
    provider = FakeProvider(
        "duckduckgo",
        [FakeItem(title="Plain result", snippet="A web page about evaluation harnesses.", url="https://ex.test/1")],
    )
    jobs = await LiveBridge([provider], pipeline).gather("evaluation")
    doc = (await store.list_documents())[0]
    assert jobs and doc.author == ""
    assert doc.metadata == {"provider": "duckduckgo"}
    assert "Authors:" not in doc.content


async def test_broken_citation_metadata_is_tolerated(pipeline: IngestionPipeline, store: FabricStore) -> None:
    class Exploding(FakeScholarlyItem):
        def citation_metadata(self) -> dict[str, str]:
            raise RuntimeError("bad provider")

    provider = FakeProvider(
        "crossref",
        [Exploding(title="Still indexed", snippet="A paper whose metadata accessor is broken.")],
    )
    jobs = await LiveBridge([provider], pipeline).gather("broken metadata")
    assert jobs and (await store.list_documents())[0].title == "Still indexed"


async def test_new_provider_taxonomy_and_rss_family_prefix(pipeline: IngestionPipeline, store: FabricStore) -> None:
    providers = [
        FakeProvider("crossref", [FakeItem("Crossref work", "A registered DOI record for a journal article.")]),
        FakeProvider("semantic_scholar", [FakeItem("S2 paper", "A paper record with citation counts attached.")]),
        FakeProvider("searxng", [FakeItem("Meta-search hit", "A keyless metasearch result from an instance.")]),
        # RSSProvider names itself `rss:<vendor>` — the family prefix must map.
        FakeProvider("rss:anthropic", [FakeItem("Feed post", "A vendor changelog entry about model updates.")]),
        FakeProvider("brand_new", [FakeItem("Unknown", "An unregistered provider defaults to web trust.")]),
    ]
    await LiveBridge(providers, pipeline).gather("taxonomy")

    by_title = {d.title: d.source_type for d in await store.list_documents()}
    assert by_title["Crossref work"] is SourceType.CROSSREF
    assert by_title["S2 paper"] is SourceType.SEMANTIC_SCHOLAR
    assert by_title["Meta-search hit"] is SourceType.WEB_PAGE
    assert by_title["Feed post"] is SourceType.RSS
    assert by_title["Unknown"] is SourceType.WEB_PAGE
