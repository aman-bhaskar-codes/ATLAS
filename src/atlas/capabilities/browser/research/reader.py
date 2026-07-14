"""Reader engine: distills HTML into Article models."""
from __future__ import annotations

import re


class ReaderEngine:
    """Extracts article content from HTML (a simplified reader-mode)."""

    def extract_article_text(self, html: str, title: str) -> str:
        """Naive extraction of main text by stripping tags and noise."""
        if not html:
            return ""

        # Remove script and style blocks
        text = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', html, flags=re.DOTALL | re.IGNORECASE)
        # Remove nav, header, footer
        text = re.sub(r'<(nav|header|footer)[^>]*>.*?</\1>', '', text, flags=re.DOTALL | re.IGNORECASE)
        # Strip all other HTML tags
        text = re.sub(r'<[^>]+>', ' ', text)
        # Normalize whitespace
        text = re.sub(r'\s+', ' ', text).strip()
        
        return text

    def extract_markdown(self, html: str, title: str) -> str:
        """Convert simplified HTML to markdown (placeholder)."""
        text = self.extract_article_text(html, title)
        return f"# {title}\n\n{text}"
