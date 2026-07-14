"""Source ranker for research capability."""
from __future__ import annotations

import urllib.parse


class SourceRanker:
    """Ranks extracted sources based on domain authority and official status."""

    def __init__(self, official_domains: set[str] | None = None) -> None:
        self.official_domains = official_domains or {
            "github.com",
            "docs.python.org",
            "developer.mozilla.org",
            "en.wikipedia.org",
            "arxiv.org"
        }

    def score_url(self, url: str) -> float:
        """Score a URL from 0.0 to 1.0 based on trust map."""
        try:
            parsed = urllib.parse.urlparse(url)
            domain = parsed.netloc.lower()
        except Exception:
            return 0.1

        if not domain:
            return 0.1

        # Strip www.
        if domain.startswith("www."):
            domain = domain[4:]

        if domain in self.official_domains or any(domain.endswith(f".{d}") for d in self.official_domains):
            return 1.0  # Official source
            
        if domain.endswith(".edu") or domain.endswith(".gov"):
            return 0.9  # High authority
            
        if domain.endswith(".org"):
            return 0.7  # Moderate authority
            
        return 0.5  # General web

    def rank(self, urls: list[str]) -> list[str]:
        """Sort a list of URLs descending by score."""
        return sorted(urls, key=self.score_url, reverse=True)
