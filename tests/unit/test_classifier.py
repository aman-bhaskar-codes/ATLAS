from __future__ import annotations

from atlas.infra.ids import CorrelationId
from atlas.infra.types import Tier, ToolRequest
from atlas.safety.classifier import TierClassifier
from atlas.safety.manifest import HardBlock, Manifest, RequiredConfirmation, Rule


def _policy_manifest() -> Manifest:
    return Manifest(
        version=1,
        allowed_paths={},
        allowed_commands={},
        whatsapp={},
        safety={
            "credential_dirs": ["~/.ssh"],
            "financial_domains": ["stripe.com"],
            "mass_deletion_threshold": 25,
        },
        rules=[
            Rule(tool="email", operation="send", tier=2),
            Rule(tool="calendar", operation="create", tier=2),
            Rule(tool="calendar", operation="update", tier=2),
            Rule(tool="calendar", operation="delete", tier=2),
            Rule(tool="contacts", operation="merge", tier=2),
            Rule(tool="contacts", operation="delete", tier=2),
            Rule(tool="browser", operation="click", tier=0),
            Rule(tool="browser", operation="type", tier=0),
            Rule(tool="browser", operation="submit", tier=2),
        ],
        hard_block=[
            HardBlock(tool="*", operation="*", match="credential_access"),
            HardBlock(tool="*", operation="*", match="financial_transaction"),
            HardBlock(tool="filesystem", operation="delete", match="mass_deletion"),
            HardBlock(tool="*", operation="*", match="edit_safety_config"),
        ],
        require_confirm=[
            RequiredConfirmation(tool="email", operation="send", match="sends_to_person"),
            RequiredConfirmation(tool="calendar", operation="create", match="invites_person"),
            RequiredConfirmation(tool="calendar", operation="update", match="invites_person"),
            RequiredConfirmation(tool="calendar", operation="delete", match="destructive_pim"),
            RequiredConfirmation(tool="contacts", operation="merge", match="destructive_pim"),
            RequiredConfirmation(tool="contacts", operation="delete", match="destructive_pim"),
            RequiredConfirmation(tool="browser", operation="click", match="financial_ui"),
            RequiredConfirmation(tool="browser", operation="click", match="destructive_ui"),
            RequiredConfirmation(tool="browser", operation="type", match="credential_entry"),
            RequiredConfirmation(tool="browser", operation="submit", match="submits_form"),
        ],
    )


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


def test_classifier_hard_blocks_every_declared_matcher() -> None:
    classifier = TierClassifier(_policy_manifest(), default_tier_on_error=2)
    cases = (
        ("credential_access", "filesystem", "read", {"path": "~/.ssh/id_ed25519"}),
        ("financial_transaction", "http", "post", {"url": "https://api.stripe.com/charge"}),
        ("mass_deletion", "filesystem", "delete", {"path": "/tmp/files", "target_count": 26}),
        ("edit_safety_config", "filesystem", "write", {"path": "config/permissions.yaml"}),
    )

    for matcher, tool, operation, args in cases:
        decision = classifier.classify(ToolRequest(
            correlation_id=CorrelationId("hard-block-test"), tool=tool, operation=operation, args=args
        ))
        assert decision.tier == Tier.BLOCK
        assert decision.decision == "deny"
        assert decision.matched_rule == f"hard_block:{matcher}"


def test_classifier_confirmation_matchers_require_approval() -> None:
    classifier = TierClassifier(_policy_manifest(), default_tier_on_error=2)
    cases = (
        ("sends_to_person", "email", "send", {"recipients": ["person@example.com"]}),
        ("invites_person", "calendar", "create", {"attendees": [{"email": "person@example.com"}]}),
        ("invites_person", "calendar", "update", {"attendees": ["person@example.com"]}),
        ("destructive_pim", "calendar", "delete", {}),
        ("destructive_pim", "contacts", "merge", {}),
        ("destructive_pim", "contacts", "delete", {}),
        ("financial_ui", "browser", "click", {"locator": {"value": "Pay now"}}),
        ("destructive_ui", "browser", "click", {"locator": {"value": "Delete account"}}),
        ("credential_entry", "browser", "type", {"locator": {"value": "password"}}),
        ("submits_form", "browser", "submit", {}),
    )

    for matcher, tool, operation, args in cases:
        decision = classifier.classify(ToolRequest(
            correlation_id=CorrelationId("confirmation-test"), tool=tool, operation=operation, args=args
        ))
        assert decision.tier == Tier.CONFIRM
        assert decision.decision == "require_confirm"
        assert decision.matched_rule == f"require_confirm:{matcher}"


def test_classifier_confirmation_matchers_do_not_match_safe_inputs() -> None:
    classifier = TierClassifier(_policy_manifest(), default_tier_on_error=2)
    cases = (
        ("email", "send", {"recipients": []}, Tier.CONFIRM),
        ("calendar", "create", {"attendees": []}, Tier.CONFIRM),
        ("browser", "click", {"locator": {"value": "Continue"}}, Tier.AUTO),
        ("browser", "type", {"locator": {"value": "search query"}}, Tier.AUTO),
    )

    for tool, operation, args, expected_tier in cases:
        decision = classifier.classify(ToolRequest(
            correlation_id=CorrelationId("non-match-test"), tool=tool, operation=operation, args=args
        ))
        assert decision.tier == expected_tier
        assert decision.matched_rule == f"{tool}.{operation}"

