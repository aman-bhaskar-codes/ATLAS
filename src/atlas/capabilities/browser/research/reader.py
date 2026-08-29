"""Reader engine: distills HTML into Article models (§6, §7).

Reader-mode extraction reuses the single dependency-free extractor in
`atlas.tools.extract` (this layer sits above `atlas.tools`, so it may import it),
rather than keeping a second, weaker copy of the HTML rules. Boilerplate is
dropped and headings are preserved as markdown markers so the fabric chunker can
respect section boundaries.
"""

from __future__ import annotations

from atlas.tools.extract import html_to_text


class ReaderEngine:
    """Extracts article content from HTML (structure-aware reader-mode)."""

    def extract_article_text(self, html: str, title: str) -> str:
        """Main prose with boilerplate (nav/header/footer/scripts) removed."""
        _title, text = html_to_text(html, title_hint=title)
        return text

    def extract_markdown(self, html: str, title: str) -> str:
        """Reader text rendered as markdown under an H1 title."""
        found_title, text = html_to_text(html, title_hint=title)
        heading = found_title or title
        return f"# {heading}\n\n{text}" if heading else text
