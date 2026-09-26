"""IDE tool ↔ safety manifest alignment.

The funnel is deny-by-default, so an ``ide`` operation the adapter accepts but the
manifest never names would be denied at the outer dispatch (dead capability); an
operation the manifest names but the adapter rejects is dead permission. This test
pins both directions plus the tier each operation may carry: the pure reads stay
AUTO; the two mutating verbs sit at NOTIFY here because their REAL tiering happens
on the inner ``filesystem.write`` / ``shell.run`` dispatch each performs.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from atlas.capabilities.ide.agent_tool import IDE_OPERATIONS
from atlas.infra.types import Tier

CONFIG = Path(__file__).resolve().parents[2] / "config" / "permissions.yaml"

# operation → highest tier it may carry at the OUTER `ide` boundary.
EXPECTED: dict[str, Tier] = {
    "tree": Tier.AUTO,
    "read_document": Tier.AUTO,
    "project_model": Tier.AUTO,
    "git_status": Tier.AUTO,
    "git_diff": Tier.AUTO,
    "apply_change": Tier.NOTIFY,
    "run_command": Tier.NOTIFY,
}


def _ide_rules() -> dict[str, int]:
    manifest = yaml.safe_load(CONFIG.read_text())
    return {r["operation"]: int(r["tier"]) for r in manifest["rules"] if r.get("tool") == "ide"}


def test_every_ide_operation_has_an_explicit_tier() -> None:
    rules = _ide_rules()
    assert set(rules) == set(EXPECTED), "manifest and IDE tool operations drifted apart"
    for operation, tier in EXPECTED.items():
        assert rules[operation] == int(tier), f"{operation} is tiered {rules[operation]}, expected {int(tier)}"


def test_manifest_matches_the_adapters_advertised_operations() -> None:
    # No manifest hole (deny-by-default) and no dead permission: the two sets agree.
    assert set(_ide_rules()) == set(IDE_OPERATIONS)


def test_mutating_verbs_are_not_auto_approved_at_the_boundary() -> None:
    rules = _ide_rules()
    # apply_change writes bytes and run_command runs a process — neither may be a
    # silent AUTO even though the inner funnel is the real gate.
    assert rules["apply_change"] >= int(Tier.NOTIFY)
    assert rules["run_command"] >= int(Tier.NOTIFY)
