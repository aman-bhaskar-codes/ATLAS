"""Permission manifest: typed load + machine verification.

WHY a verifier: the manifest names hard-block matchers and per-tool rules; if a
named matcher isn't implemented, or a tool exposes an operation with no rule,
that is a SILENT hole in deny-by-default. verify_manifest() turns that into a
loud CI failure (ADR-009).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class Rule(BaseModel):
    model_config = {"frozen": True}
    tool: str
    operation: str
    tier: int
    constraint: str | None = None


class HardBlock(BaseModel):
    model_config = {"frozen": True}
    tool: str
    operation: str
    match: str


class Manifest(BaseModel):
    model_config = {"frozen": True}
    version: int
    allowed_paths: dict[str, list[str]]
    allowed_commands: dict[str, list[str]]
    whatsapp: dict[str, Any]
    safety: dict[str, Any]
    rules: list[Rule]
    hard_block: list[HardBlock]


class ManifestReport(BaseModel):
    ok: bool
    missing_rules: list[str]
    orphan_rules: list[str]
    unmatched_constraints: list[str]
    hard_block_gaps: list[str]


def load_manifest(raw: dict[str, Any]) -> Manifest:
    return Manifest(**raw)


def _wildcard_covers(covered: set[tuple[str, str]], tool: str, op: str) -> bool:
    return ("*", "*") in covered or (tool, "*") in covered or ("*", op) in covered


def verify_manifest(
    manifest: Manifest,
    registered: dict[str, list[str]],
    known_constraints: set[str],
    known_matchers: set[str],
) -> ManifestReport:
    covered = {(r.tool, r.operation) for r in manifest.rules}
    reg_pairs = {(t, o) for t, ops in registered.items() for o in ops}

    missing = [
        f"{t}.{o}" for t, ops in registered.items() for o in ops
        if (t, o) not in covered and not _wildcard_covers(covered, t, o)
    ]
    orphan = [
        f"{r.tool}.{r.operation}" for r in manifest.rules
        if r.tool != "*" and (r.tool, r.operation) not in reg_pairs
    ]
    bad_constraints = [
        f"{r.tool}.{r.operation}->{r.constraint}" for r in manifest.rules
        if r.constraint is not None and r.constraint not in known_constraints
    ]
    hb_gaps = [hb.match for hb in manifest.hard_block if hb.match not in known_matchers]

    # orphans are a warning (rule for a not-yet-built tool); the rest fail.
    ok = not (missing or bad_constraints or hb_gaps)
    return ManifestReport(
        ok=ok, missing_rules=missing, orphan_rules=orphan,
        unmatched_constraints=bad_constraints, hard_block_gaps=hb_gaps,
    )
