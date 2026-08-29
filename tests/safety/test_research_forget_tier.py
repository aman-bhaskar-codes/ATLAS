"""Research `forget` ↔ safety tiering.

`forget` is the only destructive knowledge operation. The funnel must gate it:
a narrow, single-target forget is CONFIRM (explicit approval), while a
corpus-wide forget (all / source_type / uri) or a cascading session forget is
DANGEROUS (approval + confirmation code). A dry_run preview mutates nothing, so
it must NOT be escalated past CONFIRM — previewing what a forget would remove is
part of the approval flow, not the deletion.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from atlas.infra.types import Tier, ToolRequest
from atlas.safety.classifier import TierClassifier
from atlas.safety.manifest import load_manifest

CONFIG = Path(__file__).resolve().parents[2] / "config" / "permissions.yaml"


def _classifier() -> TierClassifier:
    manifest = load_manifest(yaml.safe_load(CONFIG.read_text()))
    return TierClassifier(manifest, default_tier_on_error=2)


def _classify(**args: object) -> tuple[Tier, str]:
    req = ToolRequest(
        correlation_id="cid-forget",  # type: ignore[arg-type]
        tool="knowledge",
        operation="forget",
        args={"operation": "forget", **args},
    )
    decision = _classifier().classify(req)
    return decision.tier, decision.decision


def test_narrow_forget_is_confirm() -> None:
    for scope in ("evidence", "chunk", "document"):
        tier, decision = _classify(scope=scope, target="x1")
        assert tier == Tier.CONFIRM, f"{scope} should be CONFIRM, got {tier}"
        assert decision == "require_confirm"


def test_corpus_wide_forget_is_dangerous() -> None:
    for scope in ("all", "source_type", "uri"):
        tier, decision = _classify(scope=scope, target="web_page")
        assert tier == Tier.DANGEROUS, f"{scope} should be DANGEROUS, got {tier}"
        assert decision == "require_confirm"


def test_cascading_session_forget_is_dangerous() -> None:
    tier, _ = _classify(scope="session", target="rs_1", cascade_documents=True)
    assert tier == Tier.DANGEROUS
    # A session forget that does NOT cascade only removes the session row.
    tier_no_cascade, _ = _classify(scope="session", target="rs_1")
    assert tier_no_cascade == Tier.CONFIRM


def test_dry_run_preview_is_never_escalated() -> None:
    # A preview mutates nothing — even a corpus-wide preview stays at CONFIRM.
    tier, decision = _classify(scope="all", target="", dry_run=True)
    assert tier == Tier.CONFIRM
    assert decision == "require_confirm"


def test_forget_is_never_auto_approved() -> None:
    # Whatever the scope, a forget is never silent (tier >= CONFIRM).
    for scope in ("evidence", "chunk", "document", "session", "source_type", "uri", "all"):
        tier, _ = _classify(scope=scope, target="x")
        assert tier >= Tier.CONFIRM
