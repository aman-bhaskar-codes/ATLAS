"""CitationEngine — citations are BUILT from Evidence, never generated (§34).

The model never invents URLs: every citation object is constructed from an
Evidence record's pinned uri/title/quote. Answers reference citations as [n];
the engine renders the reference list and validates marker consistency.
"""

from __future__ import annotations

from atlas.knowledge.domain import Citation, Evidence


class CitationEngine:
    def build(self, evidence: list[Evidence]) -> list[Citation]:
        """One citation per evidence record, numbered from 1 in given order."""
        citations: list[Citation] = []
        for i, ev in enumerate(evidence, start=1):
            citations.append(
                Citation(
                    index=i,
                    evidence_id=ev.evidence_id,
                    document_id=ev.document_id,
                    title=ev.title or ev.uri or ev.document_id,
                    uri=ev.uri,
                    quote=ev.quote,
                    authority=ev.authority,
                )
            )
        return citations

    def render_markdown(self, citations: list[Citation]) -> str:
        if not citations:
            return ""
        lines = ["", "### Sources"]
        for c in citations:
            if c.uri.startswith(("http://", "https://", "file://")):
                uri = f" <{c.uri}>"
            elif c.uri:
                uri = f" `{c.uri}`"
            else:
                uri = ""
            quote = c.quote[:160].replace("\n", " ")
            long_quote = len(c.quote) > 160
            rendered = f"[{c.index}] **{c.title}**{uri} — “{quote}{'…' if long_quote else ''}”"
            lines.append(rendered)
        return "\n".join(lines)

    def validate_markers(self, answer_text: str, citations: list[Citation]) -> tuple[str, bool]:
        """Strip citation markers that reference non-existent citations (§124).

        Returns (cleaned_text, all_markers_valid).
        """
        valid = {c.index for c in citations}
        cleaned: list[str] = []
        i = 0
        ok = True
        while i < len(answer_text):
            if answer_text[i] == "[" and i + 2 < len(answer_text):
                j = answer_text.find("]", i)
                if j != -1 and j - i <= 6 and answer_text[i + 1 : j].isdigit():
                    n = int(answer_text[i + 1 : j])
                    if n not in valid:
                        ok = False
                        i = j + 1  # drop invalid marker
                        continue
            cleaned.append(answer_text[i])
            i += 1
        return "".join(cleaned), ok
