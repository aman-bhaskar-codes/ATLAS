"""Deterministic lexical catalog search (Part 2 §25-§28).

Deliberately NOT semantic: this is a transparent scoring function over field
token overlap with an exact-name bonus. Every hit explains itself via
``matched_fields``; the score is reproducible from the record + query alone.
Part 6 will layer semantic scores onto ``CatalogMatch`` without changing this
contract — the fields already exist there.
"""

from __future__ import annotations

from atlas.tooling.catalog.index import CatalogIndex, _IndexedTool, _tokens
from atlas.tooling.catalog.models import CatalogMatch

# Field weights — deterministic, documented, config can override later.
_WEIGHTS: dict[str, float] = {
    "name": 0.35,
    "operation": 0.25,
    "capability": 0.15,
    "tag": 0.15,
    "description": 0.10,
}
_EXACT_NAME_BONUS = 0.30
_NAMESPACE_FIELD = "namespace"  # match context, not scored


def _field_score(indexed: _IndexedTool, query_tokens: frozenset[str], field: str) -> tuple[float, bool]:
    """(coverage 0..1 for the field, any-match flag) for one query."""
    if not query_tokens:
        return 0.0, False
    tokens = {
        "name": indexed.name_tokens,
        "operation": indexed.op_tokens,
        "capability": indexed.capability_tokens,
        "tag": indexed.tag_tokens,
        "description": indexed.description_tokens,
    }[field]
    if not tokens:
        return 0.0, False
    matched = len(query_tokens & tokens)
    return matched / len(query_tokens), matched > 0


def lexical_search(
    index: CatalogIndex,
    query: str,
    *,
    limit: int = 20,
    statuses: frozenset[str] | None = None,
) -> list[CatalogMatch]:
    """Rank tools for a free-text query. Deterministic: same index + query ->
    same ranked list (ties broken by tool_id)."""
    query_tokens = _tokens(query)
    normalized_query = " ".join(sorted(query_tokens))
    matches: list[CatalogMatch] = []
    for tool_id in sorted(index._by_id):
        indexed = index._by_id[tool_id]
        if statuses is not None and indexed.record.status.value not in statuses:
            continue
        score = 0.0
        matched_fields: list[str] = []
        for field_name, weight in _WEIGHTS.items():
            coverage, any_match = _field_score(indexed, query_tokens, field_name)
            if any_match:
                matched_fields.append(field_name)
                score += weight * coverage
        # namespace is a context field: it can qualify a match but adds no score
        if query_tokens & indexed.namespace_tokens:
            matched_fields.append(_NAMESPACE_FIELD)
        if normalized_query and normalized_query == " ".join(sorted(indexed.name_tokens)):
            score += _EXACT_NAME_BONUS
        if not matched_fields or score <= 0.0:
            continue
        matches.append(
            CatalogMatch(
                tool_id=tool_id,
                score=round(min(1.0, score), 4),
                matched_fields=tuple(matched_fields),
                record=indexed.record,
            )
        )
    matches.sort(key=lambda m: (-m.score, m.tool_id))
    return matches[: max(1, limit)]


__all__ = ["lexical_search"]
