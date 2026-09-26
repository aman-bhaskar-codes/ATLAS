"""Research surface ↔ knowledge manifest alignment (Slice R2).

The research surface is not a second execution path: it drives ONLY the governed
``knowledge`` tool, and only through operations the safety manifest names. This
test pins that guarantee structurally — every mode ``ResearchService`` may dispatch
(``_VALID_MODES``) must be a knowledge operation the manifest grants, and the
service must never drive the destructive ``forget`` op. If either drifts, the
research surface has grown a hole (an ungoverned mode) or dead configuration.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from atlas.orchestration.research.service import _VALID_MODES, KNOWLEDGE_TOOL

CONFIG = Path(__file__).resolve().parents[2] / "config" / "permissions.yaml"


def _knowledge_operations() -> set[str]:
    manifest = yaml.safe_load(CONFIG.read_text())
    return {r["operation"] for r in manifest["rules"] if r.get("tool") == "knowledge"}


def test_research_drives_only_the_governed_knowledge_tool() -> None:
    # The service dispatches this exact tool name — the manifest seat it re-enters.
    assert KNOWLEDGE_TOOL == "knowledge"


def test_every_research_mode_is_a_manifested_knowledge_operation() -> None:
    granted = _knowledge_operations()
    missing = _VALID_MODES - granted
    assert not missing, f"research modes with no manifest seat (governance hole): {missing}"


def test_research_never_drives_the_destructive_forget_op() -> None:
    # `forget` is destructive (CONFIRM-tier); the read/search research surface must
    # not be able to reach it, even though it is a valid knowledge operation.
    assert "forget" not in _VALID_MODES
