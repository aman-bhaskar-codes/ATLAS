"""Query understanding — routing, decomposition, bounded rewrites (§11-14).

Deterministic signals FIRST (§12): lexical cues decide the route without a
model call whenever they are unambiguous; the model is consulted only as a
tie-breaker. Rewrites are bounded (2-4 variants) and cheap; decomposition for
multi-hop questions is capped at 4 sub-questions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from atlas.knowledge.domain import QueryRoute

_MEMORY_CUES = (
    "my ",
    "i told you",
    "remember",
    "yesterday",
    "last week",
    "last time",
    "we discussed",
    "earlier today",
    "my preference",
    "my project",
    "my email",
    "my calendar",
    "what did i",
    "when did i",
)
_CODEBASE_CUES = (
    "codebase",
    "source code",
    "in the code",
    "function",
    "class ",
    "module",
    "repo",
    "repository",
    "file ",
    "import ",
    "def ",
    "src/",
    "tests/",
    "architecture of atlas",
    "atlas source",
)
_RESEARCH_CUES = ("research", "investigate", "deep dive", "survey", "compare sources", "find out everything")
_LIVE_CUES = (
    "today",
    "this week",
    "latest",
    "recent",
    "current",
    "now",
    "breaking",
    "just announced",
    "this month",
    "price of",
    "weather",
    "news",
)
_MULTI_HOP_CUES = (" and then ", "depends on", "difference between", "compare", " vs ", "why does", "how does")
_COMPUTE_RE = re.compile(r"^\s*[-+*/().\d\s]+\s*[?]?s*$")
_QUESTION_SPLIT = re.compile(r"[?]\s+")


@dataclass(frozen=True)
class QueryPlan:
    """The understood query: route + variants to retrieve + sub-questions."""

    text: str
    route: QueryRoute
    rewrites: tuple[str, ...] = ()  # 2-4 bounded variants (§14)
    sub_questions: tuple[str, ...] = ()  # for multi-hop (§13)
    freshness_required: bool = False
    signals: tuple[str, ...] = field(default_factory=tuple)


class QueryRouter:
    """Deterministic-first routing; never needs a model to answer."""

    def route(self, query: str) -> QueryPlan:
        low = query.lower()
        signals: list[str] = []

        if _COMPUTE_RE.match(query) and any(ch.isdigit() for ch in query):
            return QueryPlan(text=query, route=QueryRoute.COMPUTATIONAL, signals=("arithmetic",))

        freshness = any(c in low for c in _LIVE_CUES)
        if freshness:
            signals.append("freshness_cue")

        memory_hits = sum(1 for c in _MEMORY_CUES if c in low)
        code_hits = sum(1 for c in _CODEBASE_CUES if c in low)
        research_hit = any(c in low for c in _RESEARCH_CUES)
        multi_hop = sum(1 for c in _MULTI_HOP_CUES if c in low)
        n_questions = len([q for q in _QUESTION_SPLIT.split(query) if q.strip()])

        if memory_hits >= 1 and memory_hits >= code_hits:
            signals.append(f"memory_cues={memory_hits}")
            route = QueryRoute.PRIVATE_KNOWLEDGE
        elif code_hits >= 1:
            signals.append(f"codebase_cues={code_hits}")
            route = QueryRoute.CODEBASE
        elif research_hit:
            signals.append("research_cue")
            route = QueryRoute.RESEARCH
        elif multi_hop >= 1 or n_questions > 1:
            signals.append(f"multi_hop={multi_hop},questions={n_questions}")
            route = QueryRoute.MULTI_HOP
        elif freshness:
            route = QueryRoute.LIVE
        else:
            route = QueryRoute.MIXED  # fabric decides: local index first, live if needed

        rewrites = self._rewrite(query)
        subs = self._decompose(query) if route is QueryRoute.MULTI_HOP else ()
        return QueryPlan(
            text=query,
            route=route,
            rewrites=rewrites,
            sub_questions=subs,
            freshness_required=freshness,
            signals=tuple(signals),
        )

    def _rewrite(self, query: str) -> tuple[str, ...]:
        """2-4 bounded variants: original + keyword-dense + question-stripped."""
        variants = [query]
        stripped = re.sub(r"^(what|who|when|where|why|how)\s+(is|are|was|were|does|do|did)\s+", "", query, flags=re.I)
        stripped = stripped.rstrip("?").strip()
        if stripped and stripped.lower() != query.lower():
            variants.append(stripped)
        keywords = " ".join(w for w in re.findall(r"[A-Za-z0-9_]{4,}", query) if w.lower() not in _NOISE)[:200]
        if keywords and keywords != query:
            variants.append(keywords)
        return tuple(variants[:4])

    def _decompose(self, query: str) -> tuple[str, ...]:
        """Deterministic decomposition: split compound questions, cap 4 (§13)."""
        parts = [
            p.strip() + ("?" if "?" in query and not p.strip().endswith(("?", ".")) else "")
            for p in _QUESTION_SPLIT.split(query)
            if len(p.strip()) > 10
        ]
        if len(parts) > 1:
            return tuple(parts[:4])
        if " and " in query.lower():
            halves = re.split(r"\s+and\s+", query, maxsplit=2, flags=re.I)
            if len(halves) > 1 and all(len(h.strip()) > 10 for h in halves):
                return tuple(h.strip() for h in halves[:4])
        return ()


_NOISE = frozenset(
    """what who when where why how is are was were does do did the and for with
    about into from this that there here can could would should please tell
    explain describe regarding""".split()
)
