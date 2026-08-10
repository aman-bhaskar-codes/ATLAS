from __future__ import annotations

from atlas.infra.types import Tier, ToolRequest
from atlas.safety.classifier import TierClassifier
from atlas.safety.manifest import Manifest


def test_classifier_rule_match() -> None:
    m = Manifest(
        version=1,
        allowed_paths={}, allowed_commands={}, whatsapp={}, safety={},
        rules=[
            {"tool": "fs", "operation": "read", "tier": 0},  # type: ignore
            {"tool": "fs", "operation": "write", "tier": 1},  # type: ignore
            {"tool": "db", "operation": "drop", "tier": 3},  # type: ignore  — DANGEROUS
            {"tool": "nuke", "operation": "all", "tier": 4},  # type: ignore  — BLOCK
        ],
        hard_block=[]
    )
    clf = TierClassifier(m, default_tier_on_error=2)

    # Tier 0 (AUTO) → allow
    req1 = ToolRequest(correlation_id="cid-1", tool="fs", operation="read")  # type: ignore
    d1 = clf.classify(req1)
    assert d1.tier == Tier.AUTO
    assert d1.decision == "allow"

    # Tier 1 (NOTIFY) → allow
    req2 = ToolRequest(correlation_id="cid-2", tool="fs", operation="write")  # type: ignore
    d2 = clf.classify(req2)
    assert d2.tier == Tier.NOTIFY
    assert d2.decision == "allow"

    # Tier 3 (DANGEROUS) → require_confirm (with confirmation code in engine)
    req3 = ToolRequest(correlation_id="cid-3", tool="db", operation="drop")  # type: ignore
    d3 = clf.classify(req3)
    assert d3.tier == Tier.DANGEROUS
    assert d3.decision == "require_confirm"

    # Tier 4 (BLOCK) → deny
    req4 = ToolRequest(correlation_id="cid-5", tool="nuke", operation="all")  # type: ignore
    d4 = clf.classify(req4)
    assert d4.tier == Tier.BLOCK
    assert d4.decision == "deny"

    # Unknown tool → deny-by-default
    req_unk = ToolRequest(correlation_id="cid-4", tool="unknown", operation="do")  # type: ignore
    d_unk = clf.classify(req_unk)
    assert d_unk.decision == "deny"
    assert d_unk.reason.startswith("deny-by-default")

