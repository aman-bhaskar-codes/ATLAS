from __future__ import annotations

from atlas.infra.types import ToolRequest
from atlas.safety.classifier import TierClassifier
from atlas.safety.manifest import Manifest


def test_classifier_rule_match() -> None:
    m = Manifest(
        version=1,
        allowed_paths={}, allowed_commands={}, whatsapp={}, safety={},
        rules=[
            {"tool": "fs", "operation": "read", "tier": 0},  # type: ignore
            {"tool": "fs", "operation": "write", "tier": 1},  # type: ignore
            {"tool": "db", "operation": "drop", "tier": 3},  # type: ignore
        ],
        hard_block=[]
    )
    clf = TierClassifier(m, default_tier_on_error=2)

    req1 = ToolRequest(correlation_id="cid-1", tool="fs", operation="read")  # type: ignore
    d1 = clf.classify(req1)
    assert d1.tier == 0
    assert d1.decision == "allow"

    req2 = ToolRequest(correlation_id="cid-2", tool="fs", operation="write")  # type: ignore
    d2 = clf.classify(req2)
    assert d2.tier == 1
    assert d2.decision == "allow"

    req3 = ToolRequest(correlation_id="cid-3", tool="db", operation="drop")  # type: ignore
    d3 = clf.classify(req3)
    assert d3.tier == 3
    assert d3.decision == "deny"

    req_unk = ToolRequest(correlation_id="cid-4", tool="unknown", operation="do")  # type: ignore
    d_unk = clf.classify(req_unk)
    assert d_unk.decision == "deny"
    assert d_unk.reason.startswith("deny-by-default")
