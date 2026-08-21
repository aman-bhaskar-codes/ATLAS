"""Parser + chunking tests (§18-22): structure awareness, tables, dispatch."""

from __future__ import annotations

from atlas.knowledge.chunking import DEFAULT_MAX_CHARS, DEFAULT_OVERLAP, chunk_parsed
from atlas.knowledge.parsers import (
    parse_code,
    parse_content,
    parse_csv,
    parse_html,
    parse_json,
    parse_markdown,
)

MD = """# Fabric Guide

Intro paragraph that explains the guide in enough words to survive chunking.

## Retrieval

| mode | leg |
| --- | --- |
| hybrid | bm25 + vector |

Hybrid retrieval fuses lexical and dense results with reciprocal rank fusion.
Final paragraph of the retrieval section with a few more words here.

## Safety

Injection scanning happens during normalization, before anything is indexed.
"""


def test_markdown_parser_finds_title_and_sections() -> None:
    parsed = parse_markdown(MD)
    assert parsed.title == "Fabric Guide"
    assert parsed.kind == "markdown"
    headings = [s.heading for s in parsed.sections]
    assert headings == ["Fabric Guide", "Retrieval", "Safety"]


def test_html_parser_strips_chrome_and_maps_headings() -> None:
    html = """
    <html><head><title>Steam Engines</title>
    <script>var x = 1;</script><style>.a{}</style></head>
    <body><h1>Steam Engines</h1><p>Newcomen built the first practical engine.</p>
    <h2>Efficiency</h2><p>Watt improved efficiency dramatically.</p></body></html>
    """
    parsed = parse_html(html)
    assert parsed.title == "Steam Engines"
    assert "var x" not in parsed.text
    assert "Newcomen built the first practical engine." in parsed.text
    assert any(s.heading == "Efficiency" for s in parsed.sections)


def test_json_parser_flattens_nested_values() -> None:
    parsed = parse_json('{"a": 1, "b": {"c": "x"}, "list": [1, 2]}')
    assert parsed.kind == "json"
    assert "a: 1" in parsed.text
    assert "b.c: x" in parsed.text
    assert "list[1]: 2" in parsed.text


def test_json_parser_falls_back_to_text_on_invalid_json() -> None:
    parsed = parse_json("{not valid json")
    assert parsed.kind == "text"
    assert "{not valid json" in parsed.text


def test_csv_parser_renders_rows_as_key_value_pairs() -> None:
    parsed = parse_csv("name,value\nalpha,1\nbeta,2\n")
    assert "name=alpha" in parsed.text and "value=2" in parsed.text


def test_code_parser_detects_definitions() -> None:
    code = "import os\n\ndef alpha():\n    return 1\n\nclass Beta:\n    pass\n"
    parsed = parse_code(code)
    headings = [s.heading for s in parsed.sections]
    assert any(h.startswith("def alpha") for h in headings)
    assert any(h.startswith("class Beta") for h in headings)


def test_parse_content_sniffs_kind_from_content_and_uri() -> None:
    assert parse_content("<html><body>x</body></html>").kind == "html"
    assert parse_content('{"a": 1}').kind == "json"
    assert parse_content("# Heading\n\ntext body").kind == "markdown"
    assert parse_content("notes", uri="file:///x/notes.md").kind == "markdown"
    assert parse_content("plain words only").kind == "text"
    assert parse_content("notes", content_type="text/markdown").kind == "markdown"


def test_chunking_keeps_tables_whole_and_tags_kind() -> None:
    parsed = parse_markdown(MD)
    chunks = chunk_parsed(parsed, "doc_1")
    tables = [c for c in chunks if c.kind == "table"]
    assert tables, "expected at least one table chunk"
    table = tables[0]
    assert table.content.strip().startswith("|")
    assert "| hybrid | bm25 + vector |" in table.content


def test_chunking_attributes_headings_to_chunks() -> None:
    parsed = parse_markdown(MD)
    chunks = chunk_parsed(parsed, "doc_1")
    headings = {c.heading for c in chunks}
    assert "Retrieval" in headings
    assert "Safety" in headings


def test_chunking_respects_size_budget_and_indices() -> None:
    body = "\n\n".join(f"Paragraph number {i} with filler words to make it long enough to count." for i in range(80))
    parsed = parse_markdown(f"# Big\n\n{body}")
    chunks = chunk_parsed(parsed, "doc_big")
    assert len(chunks) > 1
    for c in chunks:
        assert len(c.content) <= DEFAULT_MAX_CHARS + DEFAULT_OVERLAP + 2
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
    assert all(c.total_chunks == len(chunks) for c in chunks)
    assert all(c.document_id == "doc_big" for c in chunks)


def test_chunking_empty_text_yields_no_chunks() -> None:
    parsed = parse_markdown("   \n")
    assert chunk_parsed(parsed, "doc_empty") == []


def test_chunking_plain_text_without_sections() -> None:
    parsed = parse_content("Just a sentence that is long enough to be a unit on its own here.")
    chunks = chunk_parsed(parsed, "doc_plain")
    assert len(chunks) == 1
    assert chunks[0].heading == ""
    assert chunks[0].token_estimate >= 1
