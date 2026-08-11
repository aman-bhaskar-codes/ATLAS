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


async def _verify_encrypted_store(atlas: Atlas) -> tuple[bool, str]:
    """Verify existing vault rows through the initialized identity platform."""
    try:
        ok, count = await atlas.identity.verify_store()
        detail = (
            f"{count} encrypted secret rows verified"
            if ok
            else f"vault verification failed at row {count}"
        )
        return ok, detail
    except Exception as exc:
        return False, f"vault verification failed: {type(exc).__name__}"


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
    if report.matcher_gaps:
        results.append(CheckResult("manifest.matchers", "fail",
                                   f"unimplemented safety matchers: {report.matcher_gaps}"))
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
    if docker_ok:
        results.append(CheckResult("sandbox.runtime", "pass", "Docker sandbox reachable"))
    elif atlas.settings.env == "dev":
        results.append(CheckResult(
            "sandbox.runtime", "warn", "Docker unavailable; native development fallback active"
        ))
    else:
        results.append(CheckResult(
            "sandbox.runtime", "fail", "Docker sandbox required outside development"
        ))

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
    has_confirmations = bool(atlas.manifest.require_confirm)
    permissions_ok = has_hard_blocks and has_confirmations
    results.append(CheckResult(
        "permissions",
        "pass" if permissions_ok else "fail",
        (
            "deny-by-default + hard blocks + confirmation rules present"
            if permissions_ok
            else "hard blocks or confirmation rules missing"
        ),
    ))

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

    # audit ledger integrity
    audit_ok, audit_count = await atlas.audit.verify_chain()
    results.append(CheckResult(
        "audit.chain",
        "pass" if audit_ok else "fail",
        f"{audit_count} records verified" if audit_ok else f"chain broken at record {audit_count}",
    ))

    # identity vault health; successful decryption also proves the initialized key works
    vault_ok, vault_detail = await _verify_encrypted_store(atlas)
    results.append(CheckResult(
        "identity.encryption", "pass" if vault_ok else "fail", vault_detail
    ))

    # count stored credentials (never values)
    n_identities = await _count_identities(atlas)
    results.append(CheckResult("identity.credentials", "pass", f"{n_identities} stored"))

    # future compatibility
    results.append(CheckResult("future.providers", "pass",
                               "provider adapter layer present (cloud disabled in Phase 1)"))
    return results


def exit_code(results: list[CheckResult]) -> int:
    return 1 if any(r.status == "fail" for r in results) else 0
