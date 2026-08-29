"""ResearchTool tests — the agent's door into the Knowledge Fabric.

Fakes only. What matters here is contract behaviour, not fabric internals:
every operation returns a `ToolResult` (never raises), a failed round SAYS it
found nothing instead of implying coverage, retrieved content is labelled
untrusted DATA, and `dry_run` never touches the network.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from atlas.tools.research import MAX_ANSWER_CHARS, HttpTextFetcher, ResearchTool, _html_to_text


# ── fabric / runner / pipeline doubles ──────────────────────────────────
class FakeCitation:
    def __init__(self, index: int, title: str, uri: str, quote: str) -> None:
        self.index = index
        self.title = title
        self.uri = uri
        self.quote = quote


class FakeContradiction:
    def __init__(self, key: str, description: str) -> None:
        self.key = key
        self.description = description


class FakeEvidence:
    def __init__(self, uri: str, title: str, authority: float) -> None:
        self.uri = uri
        self.title = title
        self.document_id = "doc_1"
        self.source = "arxiv"
        self.authority = authority


class FakeAnswer:
    def __init__(
        self,
        *,
        text: str = "Adaptive evaluation is measured with held-out benchmarks [1].",
        answered: bool = True,
        citations: tuple[FakeCitation, ...] = (),
        contradictions: tuple[FakeContradiction, ...] = (),
        evidence: tuple[FakeEvidence, ...] = (),
        refusal_reason: str = "",
        degraded: bool = False,
    ) -> None:
        self.text = text
        self.answered = answered
        self.confidence = 0.7123
        self.mode = "research"
        self.citations = citations
        self.contradictions = contradictions
        self.evidence = evidence
        self.refusal_reason = refusal_reason
        self.degraded = degraded
        self.degradation_reason = "vector store offline" if degraded else ""


class FakeFabric:
    def __init__(self, answer: FakeAnswer | None = None, *, fail: bool = False) -> None:
        self.answer = answer or FakeAnswer(citations=(FakeCitation(1, "Paper", "https://ex.test/p", "a quote"),))
        self.fail = fail
        self.calls: list[tuple[str, tuple[str, ...] | None]] = []

    async def query(self, text: str, *, mode: Any = None, source_types: tuple[str, ...] | None = None) -> FakeAnswer:
        self.calls.append((text, source_types))
        if self.fail:
            raise RuntimeError("retriever exploded")
        return self.answer


class FakeQuestion:
    def __init__(self, text: str, status: str, summary: str) -> None:
        self.text = text
        self.status = status
        self.answer_summary = summary


class FakeDoc:
    def __init__(self, title: str, uri: str) -> None:
        self.title = title
        self.uri = uri


class FakeChunk:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeCandidate:
    def __init__(self, title: str, uri: str, content: str) -> None:
        self.document = FakeDoc(title, uri)
        self.chunk = FakeChunk(content)


class FakeSession:
    def __init__(self) -> None:
        self.session_id = "rs_1"
        self.goal = "autonomous adaptation evaluation"
        self.pages_used = 2
        self.questions = (FakeQuestion("how is adaptation measured?", "ANSWERED", "with held-out benchmarks"),)


class FakeOutcome:
    def __init__(self, candidates: tuple[FakeCandidate, ...], *, discovered: int = 2) -> None:
        self.session = FakeSession()
        self.candidates = candidates
        self.stop_reason = "question list processed"
        self.gains = (0.4,)
        self.discovered = discovered


class FakeRunner:
    def __init__(self, outcome: FakeOutcome | None = None, *, fail: bool = False) -> None:
        self.outcome = outcome or FakeOutcome((FakeCandidate("Paper", "https://ex.test/p", "a finding"),))
        self.fail = fail
        self.calls: list[tuple[str, bool]] = []

    async def start(self, goal: str, *, resume: bool = True, budget: Any = None, rewrites: tuple[str, ...] = ()) -> Any:
        self.calls.append((goal, resume))
        if self.fail:
            raise RuntimeError("research runner exploded")
        return self.outcome


class FakeJob:
    document_id = "doc_new"
    state = "READY"


class FakePipeline:
    def __init__(self) -> None:
        self.ingested: list[dict[str, Any]] = []

    async def ingest(self, **kwargs: Any) -> FakeJob:
        self.ingested.append(kwargs)
        return FakeJob()


def _tool(**over: Any) -> ResearchTool:
    kwargs: dict[str, Any] = {"fabric": FakeFabric(), "research": FakeRunner(), "pipeline": FakePipeline()}
    kwargs.update(over)
    return ResearchTool(**kwargs)


# ── manifest seat ───────────────────────────────────────────────────────
def test_tool_uses_the_reserved_knowledge_seat() -> None:
    # `permissions.yaml` has always reserved {tool: knowledge, ...}; the tool
    # must not invent a new trust surface.
    assert ResearchTool.name == "knowledge"


# ── dry_run is pure ─────────────────────────────────────────────────────
def test_dry_run_describes_every_operation_without_calling_out() -> None:
    fabric, runner = FakeFabric(), FakeRunner()
    tool = _tool(fabric=fabric, research=runner)

    assert "no external calls" in tool.dry_run({"operation": "search", "query": "adaptation"})
    assert "BOUNDED" in tool.dry_run({"operation": "research", "goal": "adaptation"})
    assert "Fetch and index" in tool.dry_run({"operation": "read_url", "url": "https://ex.test/p"})
    assert "no external calls" in tool.dry_run({"operation": "sources", "query": "adaptation"})
    assert "Unknown" in tool.dry_run({"operation": "teleport"})
    # nothing was executed by previewing
    assert fabric.calls == [] and runner.calls == []


# ── search ──────────────────────────────────────────────────────────────
async def test_search_returns_flattened_answer_with_citations() -> None:
    fabric = FakeFabric(
        FakeAnswer(citations=(FakeCitation(1, "Paper", "https://ex.test/p", "quoted evidence"),)),
    )
    result = await _tool(fabric=fabric).execute({"operation": "search", "query": "adaptation"})

    assert result.ok
    assert result.output["answered"] is True
    assert result.output["confidence"] == 0.712  # rounded, never invented
    assert result.output["citations"][0]["uri"] == "https://ex.test/p"
    assert result.duration_ms is not None


async def test_search_parses_source_type_filters_from_a_string() -> None:
    fabric = FakeFabric()
    await _tool(fabric=fabric).execute({"operation": "search", "query": "q", "source_types": "arxiv, crossref"})
    assert fabric.calls[0][1] == ("arxiv", "crossref")


async def test_search_requires_a_query() -> None:
    result = await _tool().execute({"operation": "search"})
    assert not result.ok and "query" in (result.error or "")


async def test_unknown_operation_is_refused_not_guessed() -> None:
    result = await _tool().execute({"operation": "exfiltrate"})
    assert not result.ok and "unknown operation" in (result.error or "")


async def test_a_fabric_failure_never_kills_the_run() -> None:
    result = await _tool(fabric=FakeFabric(fail=True)).execute({"operation": "search", "query": "q"})
    assert not result.ok and "search failed" in (result.error or "")
    assert result.duration_ms is not None


# ── refusals, contradictions, degradation are surfaced verbatim ──────────
async def test_unanswered_query_carries_its_refusal_reason() -> None:
    fabric = FakeFabric(FakeAnswer(text="", answered=False, refusal_reason="no evidence above threshold"))
    result = await _tool(fabric=fabric).execute({"operation": "search", "query": "q"})
    assert result.output["answered"] is False
    assert result.output["refusal_reason"] == "no evidence above threshold"


async def test_contradictions_and_degradation_are_not_averaged_away() -> None:
    fabric = FakeFabric(
        FakeAnswer(
            contradictions=(FakeContradiction("throughput", "source A says 10x, source B says 2x"),),
            degraded=True,
        )
    )
    result = await _tool(fabric=fabric).execute({"operation": "search", "query": "q"})
    assert result.output["contradictions"][0]["key"] == "throughput"
    assert result.output["degraded"] is True
    assert result.output["degradation_reason"] == "vector store offline"


async def test_answer_text_is_bounded() -> None:
    fabric = FakeFabric(FakeAnswer(text="x" * (MAX_ANSWER_CHARS + 500)))
    result = await _tool(fabric=fabric).execute({"operation": "search", "query": "q"})
    assert len(result.output["text"]) == MAX_ANSWER_CHARS


# ── research ────────────────────────────────────────────────────────────
async def test_research_reports_session_questions_and_findings() -> None:
    runner = FakeRunner()
    result = await _tool(research=runner).execute({"operation": "research", "goal": "adaptation"})

    assert result.ok
    payload = result.output
    assert payload["session_id"] == "rs_1"
    assert payload["discovered_documents"] == 2
    assert payload["pages_used"] == 2
    assert payload["questions"][0]["status"] == "ANSWERED"
    assert payload["findings"][0]["uri"] == "https://ex.test/p"
    assert payload["answer"]["citations"]  # the synthesized, cited answer
    assert "coverage_warning" not in payload
    assert runner.calls == [("adaptation", True)]


async def test_research_with_no_findings_admits_incomplete_coverage() -> None:
    result = await _tool(research=FakeRunner(FakeOutcome((), discovered=0))).execute(
        {"operation": "research", "goal": "an unasked question"}
    )
    # §22: never imply comprehensive coverage that did not happen.
    assert "Coverage is incomplete" in result.output["coverage_warning"]
    assert result.output["findings"] == []


async def test_research_degrades_to_search_when_no_runner_is_wired() -> None:
    fabric = FakeFabric()
    result = await _tool(fabric=fabric, research=None).execute({"operation": "research", "goal": "adaptation"})
    assert result.ok
    assert "research runner unavailable" in result.output["degraded"]
    assert result.output["citations"]  # still a cited answer, not a fake round
    assert fabric.calls == [("adaptation", None)]


async def test_research_accepts_query_as_an_alias_for_goal() -> None:
    runner = FakeRunner()
    await _tool(research=runner).execute({"operation": "research", "query": "adaptation", "resume": False})
    assert runner.calls == [("adaptation", False)]


async def test_research_requires_a_goal() -> None:
    result = await _tool().execute({"operation": "research"})
    assert not result.ok and "goal" in (result.error or "")


async def test_a_runner_failure_is_reported_not_raised() -> None:
    result = await _tool(research=FakeRunner(fail=True)).execute({"operation": "research", "goal": "g"})
    assert not result.ok and "research failed" in (result.error or "")


# ── read_url (§23: retrieved content is DATA) ───────────────────────────
async def test_read_url_indexes_as_untrusted_external_data() -> None:
    pipeline = FakePipeline()

    async def fetch(url: str) -> tuple[str, str, str]:
        return ("A Paper", "Body text about adaptation.", "text/markdown")

    tool = _tool(pipeline=pipeline, fetch=fetch, web_source_type="web_page")
    result = await tool.execute({"operation": "read_url", "url": "https://ex.test/p"})

    assert result.ok and result.output["indexed"] is True
    assert result.output["document_id"] == "doc_new"
    assert "never obeyed" in result.output["note"]
    ingested = pipeline.ingested[0]
    assert ingested["provenance"]["trust"] == "untrusted_external"
    assert ingested["provenance"]["pipe"] == "tool"
    assert ingested["source_id"] == "read_url:https://ex.test/p"
    assert ingested["source_type"] == "web_page"  # injected by the composition root


async def test_read_url_reports_pages_with_no_extractable_text() -> None:
    async def fetch(url: str) -> tuple[str, str, str]:
        return ("", "   ", "application/pdf")

    result = await _tool(fetch=fetch).execute({"operation": "read_url", "url": "https://ex.test/a.pdf"})
    assert result.ok and result.output["indexed"] is False
    assert result.output["reason"] == "no extractable text"


async def test_read_url_indexes_extracted_pdf_text() -> None:
    pipeline = FakePipeline()

    async def fetch(url: str) -> tuple[str, str, str]:
        return ("A Paper", "Autonomous adaptation is measured with held-out benchmarks.", "text/markdown")

    tool = _tool(pipeline=pipeline, fetch=fetch, web_source_type="web_page")
    result = await tool.execute({"operation": "read_url", "url": "https://ex.test/a.pdf"})
    assert result.ok and result.output["indexed"] is True
    ingested = pipeline.ingested[0]
    assert "held-out benchmarks" in ingested["content"]
    assert ingested["content_type"] == "text/markdown"
    assert ingested["provenance"]["trust"] == "untrusted_external"


async def test_read_url_without_a_fetcher_fails_closed() -> None:
    result = await _tool(fetch=None).execute({"operation": "read_url", "url": "https://ex.test/p"})
    assert not result.ok and "fetcher" in (result.error or "")


async def test_read_url_requires_a_url() -> None:
    result = await _tool(fetch=lambda url: None).execute({"operation": "read_url"})
    assert not result.ok and "url" in (result.error or "")


# ── sources ─────────────────────────────────────────────────────────────
async def test_sources_lists_deduped_indexed_sources() -> None:
    fabric = FakeFabric(
        FakeAnswer(
            evidence=(
                FakeEvidence("https://ex.test/p", "Paper", 0.87654),
                FakeEvidence("https://ex.test/p", "Paper (dup)", 0.5),
                FakeEvidence("https://ex.test/q", "Other", 0.5),
            )
        )
    )
    result = await _tool(fabric=fabric).execute({"operation": "sources", "query": "adaptation"})

    uris = [s["uri"] for s in result.output["sources"]]
    assert uris == ["https://ex.test/p", "https://ex.test/q"]
    assert result.output["sources"][0]["authority"] == 0.877
    assert result.output["sources"][0]["title"] == "Paper"  # first wins, not overwritten


# ── HttpTextFetcher (bounded, no browser required) ──────────────────────
def test_html_to_text_keeps_prose_and_drops_scripts() -> None:
    title, text = _html_to_text(
        "<html><head><title> A  Paper </title><style>.x{}</style></head>"
        "<body><script>evil()</script><p>First line.</p><p>Second line.</p></body></html>"
    )
    assert title == "A Paper"
    assert "evil()" not in text and ".x{}" not in text
    assert "First line." in text and "Second line." in text


def test_html_to_text_tolerates_a_missing_title() -> None:
    title, text = _html_to_text("<html><body><p>Body only.</p></body></html>")
    assert title == ""
    assert "Body only." in text


async def test_fetcher_declines_binary_bodies_rather_than_indexing_garbage(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        headers: ClassVar[dict[str, str]] = {"content-type": "application/pdf"}
        content = b"%PDF-1.7 binary junk"
        encoding = None

        def raise_for_status(self) -> None: ...

    class FakeClient:
        def __init__(self, *a: Any, **k: Any) -> None: ...
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *exc: Any) -> None: ...
        async def get(self, url: str) -> FakeResponse:
            return FakeResponse()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    title, text, content_type = await HttpTextFetcher()("https://ex.test/a.pdf")
    assert (title, text) == ("", "")
    assert content_type == "application/pdf"


async def test_fetcher_converts_html_and_sends_a_research_user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    class FakeResponse:
        headers: ClassVar[dict[str, str]] = {"content-type": "text/html; charset=utf-8"}
        content = b"<html><head><title>Paper</title></head><body><p>Findings.</p></body></html>"
        encoding = "utf-8"

        def raise_for_status(self) -> None: ...

    class FakeClient:
        def __init__(self, *a: Any, **k: Any) -> None:
            seen.update(k)

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *exc: Any) -> None: ...
        async def get(self, url: str) -> FakeResponse:
            return FakeResponse()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    title, text, content_type = await HttpTextFetcher()("https://ex.test/p")

    assert title == "Paper" and "Findings." in text
    assert content_type == "text/markdown"
    assert "ATLAS" in seen["headers"]["User-Agent"]
    assert seen["follow_redirects"] is True


async def test_fetcher_extracts_a_pdf_text_layer(monkeypatch: pytest.MonkeyPatch) -> None:
    import zlib

    stream = zlib.compress(b"BT (Autonomous adaptation benchmarks.) Tj ET")
    stream_obj = b"4 0 obj\n<< /Length %d /Filter /FlateDecode >>\nstream\n%s\nendstream\nendobj\n" % (
        len(stream),
        stream,
    )
    pdf = b"%PDF-1.7\n" + stream_obj + b"%%EOF"

    class FakeResponse:
        headers: ClassVar[dict[str, str]] = {"content-type": "application/pdf"}
        content = pdf
        encoding = None

        def raise_for_status(self) -> None: ...

    class FakeClient:
        def __init__(self, *a: Any, **k: Any) -> None: ...
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *exc: Any) -> None: ...
        async def get(self, url: str) -> FakeResponse:
            return FakeResponse()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    title, text, content_type = await HttpTextFetcher()("https://ex.test/a.pdf")
    assert "Autonomous adaptation benchmarks." in text
    assert content_type == "text/markdown"
    assert title == ""  # this minimal PDF carries no /Title
