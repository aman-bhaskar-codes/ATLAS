"""Tier classifier — deterministic, fail-closed.

ORDER MATTERS: hard-block matchers first (a credential read is Tier 3 even if a
permissive rule would allow the tool). Then the first matching rule. Constraints
and tool hints may only RAISE the tier. No match => deny-by-default. Any internal
error => require_confirm at the configured error tier.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

from atlas.infra.logging import get_logger
from atlas.infra.types import Decision, SafetyDecision, Tier, ToolRequest
from atlas.safety import matchers
from atlas.safety.manifest import Manifest

_log = get_logger("atlas.classifier")

KNOWN_CONSTRAINTS: frozenset[str] = frozenset(
    {
        "within_write_paths",
        "cmd_in_read_only",
        "cmd_in_side_effect",
        "contact_known",
        "recipients_known",
        "attendees_known",
    }
)


class TierClassifier:
    def __init__(self, manifest: Manifest, default_tier_on_error: int) -> None:
        self._m = manifest
        self._err_tier = Tier(default_tier_on_error)

    def classify(self, req: ToolRequest) -> SafetyDecision:
        try:
            return self._classify(req)
        except Exception as exc:  # fail closed
            _log.error("classifier.error", event_type="safety", correlation_id=req.correlation_id, error=repr(exc))
            return SafetyDecision(
                decision="require_confirm",
                tier=self._err_tier,
                reason=f"classifier error, fail-closed: {exc!r}",
            )

    def _classify(self, req: ToolRequest) -> SafetyDecision:
        hb = self._hard_block(req)
        if hb is not None:
            return hb
        confirmation = self._required_confirmation(req)
        for rule in self._m.rules:
            if fnmatch.fnmatch(req.tool, rule.tool) and fnmatch.fnmatch(req.operation, rule.operation):
                tier = Tier(rule.tier)
                tier, reason = self._apply_constraint(rule.constraint, req, tier)
                matched_rule = f"{req.tool}.{req.operation}"
                if confirmation is not None:
                    required_tier, confirmation_reason, confirmation_rule = confirmation
                    tier = max(tier, required_tier)
                    reason = confirmation_reason
                    matched_rule = confirmation_rule
                if req.declared_tier_hint is not None:
                    tier = max(tier, req.declared_tier_hint)
                return self._as_decision(
                    tier,
                    reason or f"rule {req.tool}.{req.operation}",
                    matched_rule,
                )
        return SafetyDecision(
            decision="deny",
            tier=Tier.CONFIRM,
            reason="deny-by-default: no manifest rule matched",
        )

    def _hard_block(self, req: ToolRequest) -> SafetyDecision | None:
        paths = [str(v) for k, v in req.args.items() if "path" in k.lower()]
        blob = " ".join(str(v) for v in req.args.values())
        cred_dirs = list(self._m.safety.get("credential_dirs", []))
        fin_domains = list(self._m.safety.get("financial_domains", []))
        threshold = int(self._m.safety.get("mass_deletion_threshold", 25))

        for hb in self._m.hard_block:
            if not (fnmatch.fnmatch(req.tool, hb.tool) and fnmatch.fnmatch(req.operation, hb.operation)):
                continue
            hit, reason = self._match(hb.match, req, paths, blob, cred_dirs, fin_domains, threshold)
            if hit:
                _log.warning(
                    "safety.hard_block",
                    event_type="safety",
                    correlation_id=req.correlation_id,
                    match=hb.match,
                    reason=reason,
                )
                return SafetyDecision(
                    decision="deny",
                    tier=Tier.BLOCK,
                    reason=f"hard_block:{hb.match}: {reason}",
                    matched_rule=f"hard_block:{hb.match}",
                )
        return None

    def _required_confirmation(self, req: ToolRequest) -> tuple[Tier, str, str] | None:
        paths = [str(v) for k, v in req.args.items() if "path" in k.lower()]
        blob = " ".join(str(v) for v in req.args.values())
        cred_dirs = list(self._m.safety.get("credential_dirs", []))
        fin_domains = list(self._m.safety.get("financial_domains", []))
        threshold = int(self._m.safety.get("mass_deletion_threshold", 25))

        for confirmation in self._m.require_confirm:
            if not (
                fnmatch.fnmatch(req.tool, confirmation.tool) and fnmatch.fnmatch(req.operation, confirmation.operation)
            ):
                continue
            hit, reason = self._match(
                confirmation.match,
                req,
                paths,
                blob,
                cred_dirs,
                fin_domains,
                threshold,
            )
            if hit:
                tier = max(Tier.CONFIRM, Tier(confirmation.tier))
                matched_rule = f"require_confirm:{confirmation.match}"
                _log.info(
                    "safety.require_confirm",
                    event_type="safety",
                    correlation_id=req.correlation_id,
                    match=confirmation.match,
                    reason=reason,
                    tier=tier.name,
                )
                return tier, f"{matched_rule}: {reason}", matched_rule
        return None

    def _match(
        self,
        name: str,
        req: ToolRequest,
        paths: list[str],
        blob: str,
        cred_dirs: list[str],
        fin_domains: list[str],
        threshold: int,
    ) -> tuple[bool, str]:
        if name == "credential_access":
            return matchers.is_credential_access(paths, cred_dirs)
        if name == "mass_deletion":
            count = int(req.args.get("target_count", 0))
            return matchers.is_mass_deletion(count, req.args.get("path"), threshold)
        if name == "financial_transaction":
            return matchers.is_financial(req.args.get("url"), req.args.get("command"), fin_domains)
        if name == "edit_safety_config":
            hit = "permissions.yaml" in blob or ("safety" in blob and ".py" in blob)
            return hit, "attempt to edit safety config" if hit else ""
        if name == "sends_to_person":
            return matchers.is_sends_to_person(req.args)
        if name == "invites_person":
            return matchers.is_invites_person(req.args)
        if name == "destructive_pim":
            return matchers.is_destructive_pim(req.tool, req.operation)
        if name == "financial_ui":
            return matchers.is_financial_ui(req.tool, req.operation, req.args)
        if name == "destructive_ui":
            return matchers.is_destructive_ui(req.tool, req.operation, req.args)
        if name == "credential_entry":
            return matchers.is_credential_entry(req.tool, req.operation, req.args)
        if name == "submits_form":
            return matchers.is_form_submission(req.tool, req.operation)
        return False, ""

    def _apply_constraint(self, constraint: str | None, req: ToolRequest, tier: Tier) -> tuple[Tier, str | None]:
        if constraint is None:
            return tier, None
        ok, reason = self._check_constraint(constraint, req)
        if ok:
            return tier, f"constraint {constraint!r} satisfied"
        return max(tier, Tier.CONFIRM), f"constraint {constraint!r} violated: {reason}"

    def _check_constraint(self, name: str, req: ToolRequest) -> tuple[bool, str]:
        if name == "within_write_paths":
            return self._path_allowed(str(req.args.get("path", "")), "write")
        if name == "cmd_in_read_only":
            return self._cmd_allowed(str(req.args.get("command", "")), "read_only")
        if name == "cmd_in_side_effect":
            return self._cmd_allowed(str(req.args.get("command", "")), "side_effect")
        if name == "contact_known":
            known = self._m.whatsapp.get("known_contacts", [])
            c = str(req.args.get("contact", ""))
            return (c in known), f"contact {c!r} not known"
        if name in ("recipients_known", "attendees_known"):
            # Dummy implementations for future email/calendar support
            return True, "ok"
        return False, f"unknown constraint {name!r}"

    def _path_allowed(self, raw: str, mode: str) -> tuple[bool, str]:
        if not raw:
            return False, "no path"
        try:
            resolved = Path(raw).expanduser().resolve(strict=False)
        except (OSError, RuntimeError, ValueError):
            return False, "path resolution failed"
        for g in self._m.allowed_paths.get(mode, []):
            if fnmatch.fnmatch(str(resolved), str(Path(g).expanduser())):
                return True, "ok"
        return False, f"{resolved} outside allowed {mode} paths"

    def _cmd_allowed(self, cmd: str, klass: str) -> tuple[bool, str]:
        for allowed in self._m.allowed_commands.get(klass, []):
            if cmd.strip().startswith(allowed):
                return True, "ok"
        return False, f"command {cmd!r} not in {klass} allowlist"

    @staticmethod
    def _as_decision(tier: Tier, reason: str, rule: str) -> SafetyDecision:
        decision: Decision = (
            "allow" if tier <= Tier.NOTIFY else "require_confirm" if tier in (Tier.CONFIRM, Tier.DANGEROUS) else "deny"
        )
        return SafetyDecision(decision=decision, tier=tier, reason=reason, matched_rule=rule)
