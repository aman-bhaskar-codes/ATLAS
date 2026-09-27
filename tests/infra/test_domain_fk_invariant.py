"""T7 — design rule 5: NO CROSS-DOMAIN FOREIGN KEYS.

WHY this test exists: the domain-routed persistence seam lets each
:class:`~atlas.infra.storage_domains.StorageDomain` live on a SEPARATE Postgres
account. A foreign key whose two ends belong to DIFFERENT domains would be
satisfiable only because the translate-existing provisioner replicates the FULL
schema onto every account (T3 deviation) — it would silently coople two domains
and break the moment they are peeled onto distinct accounts. So every FK edge in
``infra.db._MIGRATIONS`` MUST stay within a single domain.

This parses the real migration DDL (source of truth) rather than a hand list, so
a future migration that adds a cross-domain FK — or an FK touching a table nobody
has assigned a domain — fails LOUD here instead of at a customer's split account.
"""

from __future__ import annotations

import re

from atlas.infra.db import _MIGRATIONS
from atlas.infra.storage_domains import StorageDomain

# ── table -> owning domain, for every table that participates in an FK edge ──
#
# Deliberately NOT a full catalogue of all ~114 tables: only tables on either end
# of a REFERENCES edge need a domain for the invariant, and keeping the map to the
# FK surface keeps it accurate and maintainable. The guard test below fails if a
# new FK introduces a table missing from this map, forcing a domain decision.
_TABLE_DOMAIN: dict[str, StorageDomain] = {
    # RESEARCH — knowledge fabric + retrieval store
    "knowledge_documents": StorageDomain.RESEARCH,
    "knowledge_chunks": StorageDomain.RESEARCH,
    "fabric_documents": StorageDomain.RESEARCH,
    "fabric_chunks": StorageDomain.RESEARCH,
    # MEMORY — trajectories + the learning records that hang off them
    "trajectories": StorageDomain.MEMORY,
    "decision_traces": StorageDomain.MEMORY,
    "failure_records": StorageDomain.MEMORY,
    "experiences": StorageDomain.MEMORY,
    "experience_applications": StorageDomain.MEMORY,
    # CORE — governed tooling catalogue
    "tool_sources": StorageDomain.CORE,
    "tool_namespaces": StorageDomain.CORE,
    "tool_definitions": StorageDomain.CORE,
    "tool_operations": StorageDomain.CORE,
}


def _fk_edges() -> list[tuple[str, str]]:
    """(referencing_table, referenced_table) for every FK in the migrations."""
    full = "\n".join(_MIGRATIONS)
    edges: list[tuple[str, str]] = []
    for block in re.finditer(r"CREATE TABLE IF NOT EXISTS (\w+)\s*\((.*?)\)\s*;", full, re.I | re.S):
        table, body = block.group(1), block.group(2)
        for ref in re.finditer(r"REFERENCES\s+(\w+)", body, re.I):
            edges.append((table, ref.group(1)))
    return edges


def test_migrations_actually_contain_fk_edges() -> None:
    # Guards the parser: if this drops to zero the regex silently stopped matching
    # and the invariant below would vacuously pass.
    assert len(_fk_edges()) >= 10


def test_every_fk_table_has_an_assigned_domain() -> None:
    """A new FK touching an uncatalogued table must force a domain decision."""
    edges = _fk_edges()
    seen = {t for edge in edges for t in edge}
    missing = sorted(t for t in seen if t not in _TABLE_DOMAIN)
    assert not missing, f"FK-participating tables with no assigned StorageDomain: {missing}"


def test_no_foreign_key_crosses_a_domain_boundary() -> None:
    """Design rule 5: both ends of every FK live in the SAME domain."""
    violations: list[str] = []
    for referencing, referenced in _fk_edges():
        d_from = _TABLE_DOMAIN.get(referencing)
        d_to = _TABLE_DOMAIN.get(referenced)
        if d_from is not None and d_to is not None and d_from is not d_to:
            violations.append(f"{referencing}({d_from.value}) -> {referenced}({d_to.value})")
    assert not violations, f"cross-domain foreign keys break account isolation: {violations}"
