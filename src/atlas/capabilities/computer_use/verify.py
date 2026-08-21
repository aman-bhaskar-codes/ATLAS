"""Action verification — an action is only "done" when evidence says so.

WHY explicit expectations: ControlResult.ok means the adapter call succeeded,
NOT that the goal was achieved. Verification compares before/after
PerceptionSnapshots against declarative ExpectationSpecs. Phase 47 honesty
rule: with no expectations we REFUSE to claim verification instead of faking
success.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel

from atlas.perception.contracts import PerceptionSnapshot


class ExpectationKind(StrEnum):
    URL_CONTAINS = "url_contains"
    TEXT_PRESENT = "text_present"
    TEXT_ABSENT = "text_absent"
    ELEMENT_PRESENT = "element_present"
    APP_ACTIVE = "app_active"
    STATE_CHANGED = "state_changed"


class ExpectationSpec(BaseModel):
    """One declarative assertion about the post-action world state."""

    model_config = {"frozen": True}
    kind: ExpectationKind
    value: str = ""  # url fragment / text / app name / element label
    role: str | None = None  # for element_present: role filter


class VerificationResult(BaseModel):
    model_config = {"frozen": True}
    verified: bool
    evidence: str
    detail: str | None = None


def _labels(snapshot: PerceptionSnapshot) -> list[str]:
    out = [el.label for el in snapshot.elements if el.label]
    if snapshot.text:
        out.append(snapshot.text)
    return out


def verify_snapshots(
    expectations: tuple[ExpectationSpec, ...],
    before: PerceptionSnapshot,
    after: PerceptionSnapshot,
) -> VerificationResult:
    """Check every expectation against the after-snapshot. All must pass."""
    if not expectations:
        return VerificationResult(
            verified=False,
            evidence="none",
            detail="no expectations provided; ATLAS does not claim verification without evidence",
        )
    failures: list[str] = []
    proofs: list[str] = []
    for exp in expectations:
        ok, proof = _check_one(exp, before, after)
        if ok:
            proofs.append(proof)
        else:
            failures.append(f"{exp.kind.value}({exp.value or exp.role or ''}): {proof}")
    if failures:
        return VerificationResult(verified=False, evidence="; ".join(proofs) or "none", detail="; ".join(failures))
    return VerificationResult(verified=True, evidence="; ".join(proofs))


def _check_one(exp: ExpectationSpec, before: PerceptionSnapshot, after: PerceptionSnapshot) -> tuple[bool, str]:
    kind = exp.kind
    if kind is ExpectationKind.URL_CONTAINS:
        url = after.url or ""
        return (exp.value in url), f"url={url!r}"
    if kind is ExpectationKind.APP_ACTIVE:
        app = after.app_name or ""
        return (exp.value.lower() in app.lower()), f"app={app!r}"
    if kind is ExpectationKind.TEXT_PRESENT:
        found = any(exp.value.lower() in label.lower() for label in _labels(after))
        return found, f"{len(_labels(after))} text surfaces scanned"
    if kind is ExpectationKind.TEXT_ABSENT:
        gone = not any(exp.value.lower() in label.lower() for label in _labels(after))
        return gone, "text absent" if gone else "text still present"
    if kind is ExpectationKind.ELEMENT_PRESENT:
        for el in after.elements:
            if exp.role and el.role != exp.role:
                continue
            if exp.value and (not el.label or exp.value.lower() not in el.label.lower()):
                continue
            if not exp.role and not exp.value:
                continue
            return True, f"element role={el.role} label={el.label!r}"
        return False, "matching element not found"
    if kind is ExpectationKind.STATE_CHANGED:
        before_state = dict(before.state)
        after_state = dict(after.state)
        changed = before_state != after_state or len(before.elements) != len(after.elements)
        return changed, f"state_before={sorted(before_state.items())[:3]} state_after={sorted(after_state.items())[:3]}"
    return False, f"unknown expectation kind {kind}"
