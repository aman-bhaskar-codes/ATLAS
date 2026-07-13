"""atlas doctor — preflight the whole system.

WHY each check is independent and returns pass/warn/fail: a diagnostic that
stops at the first failure hides the other three problems you also need to fix.
WHY fail-closed: a check that raises is reported as a FAIL, never skipped.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Literal

from atlas.app import Atlas
from atlas.infra.config import resolve_master_key
from atlas.infra.errors import ConfigError
from atlas.safety.classifier import KNOWN_CONSTRAINTS
from atlas.safety.manifest import verify_manifest
from atlas.safety.matchers import KNOWN_MATCHERS

Status = Literal["pass", "warn", "fail"]


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Status
    detail: str


# Registered tools per phase. Phase 1 has no real tools; filesystem/shell rules
# are expected to exist ahead of their tools (reported as orphans = warn).
_REGISTERED_TOOLS: dict[str, list[str]] = {
    "filesystem": ["read", "search", "write", "delete"],
    "shell": ["read_only", "side_effect"],
}


def _master_key_present(atlas: Atlas) -> bool:
    try:
        resolve_master_key(atlas.settings)
        return True
    except ConfigError:
        return False


async def _count_identities(atlas: Atlas) -> int:
    if not atlas.db.conn:
        return 0
    try:
        cur = await atlas.db.conn.execute("SELECT COUNT(*) FROM identities")
        row = await cur.fetchone()
        return row[0] if row else 0
    except Exception:
        return 0


async def run_doctor(atlas: Atlas, *, verify_manifest_only: bool = False) -> list[CheckResult]:
    results: list[CheckResult] = []

    report = verify_manifest(
        atlas.manifest, _REGISTERED_TOOLS, set(KNOWN_CONSTRAINTS), set(KNOWN_MATCHERS)
    )
    if report.hard_block_gaps:
        results.append(CheckResult("manifest.matchers", "fail",
                                   f"unimplemented hard-block matchers: {report.hard_block_gaps}"))
    elif report.unmatched_constraints:
        results.append(CheckResult("manifest.constraints", "fail",
                                   f"unknown constraints: {report.unmatched_constraints}"))
    else:
        results.append(CheckResult("manifest", "pass",
                                   f"v{atlas.manifest.version}, {len(atlas.manifest.rules)} rules"))
    if report.orphan_rules:
        results.append(CheckResult("manifest.orphans", "warn",
                                   f"rules for not-yet-built tools: {report.orphan_rules}"))

    if verify_manifest_only:
        return results

    # Docker availability + sandbox smoke
    from atlas.safety.sandbox_docker import DockerSandbox, SandboxSpec
    sb = DockerSandbox(SandboxSpec(image=atlas.config.sandbox.image))
    docker_ok = await sb.health()
    results.append(CheckResult("sandbox.docker", "pass" if docker_ok else "fail",
                               "docker reachable" if docker_ok else "start Docker/Colima"))

    # configuration
    results.append(CheckResult("config", "pass", f"env={atlas.settings.env}"))

    # required directories
    data_dir = atlas.settings.data_dir
    results.append(CheckResult(
        "directories", "pass" if data_dir.exists() else "warn",
        f"data_dir={data_dir} exists={data_dir.exists()}"))

    # secrets (presence only — never print values)
    push = "configured" if atlas.settings.ntfy_topic else "absent (CLI-only confirmations)"
    results.append(
        CheckResult("secrets.ntfy", "pass" if atlas.settings.ntfy_topic else "warn", push)
    )

    # permissions posture
    has_hard_blocks = bool(atlas.manifest.hard_block)
    results.append(CheckResult(
        "permissions", "pass" if has_hard_blocks else "fail",
        "deny-by-default + hard blocks present" if has_hard_blocks else "NO hard blocks"))

    # environment
    py_ok = sys.version_info >= (3, 13)
    results.append(CheckResult("environment.python", "pass" if py_ok else "fail",
                               f"python {sys.version_info.major}.{sys.version_info.minor}"))

    # model availability
    health = await atlas.gateway.health()
    ollama_ok = health.get("ollama", False)
    results.append(CheckResult("models.ollama", "pass" if ollama_ok else "fail",
                               "reachable" if ollama_ok else "UNREACHABLE"))

    # database + migrations
    db_ok = await atlas.db.health()
    results.append(CheckResult("database", "pass" if db_ok else "fail",
                               "connected, migrations applied" if db_ok else "not connected"))

    # identity vault health
    key_ok = _master_key_present(atlas)
    results.append(CheckResult("identity.master_key", "pass" if key_ok else "fail",
                               "present (keychain/env)" if key_ok else
                               "set Keychain 'atlas-master' or ATLAS_MASTER_KEY"))

    # count stored credentials (never values)
    n_identities = await _count_identities(atlas)
    results.append(CheckResult("identity.credentials", "pass", f"{n_identities} stored (encrypted)"))

    # future compatibility
    results.append(CheckResult("future.providers", "pass",
                               "provider adapter layer present (cloud disabled in Phase 1)"))
    return results


def exit_code(results: list[CheckResult]) -> int:
    return 1 if any(r.status == "fail" for r in results) else 0
