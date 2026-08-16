"""Safety bootstrap — classifier, policy, engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from atlas.infra.clock import Clock
from atlas.infra.config import AppConfig
from atlas.infra.ids import IdGenerator
from atlas.infra.types import AuditRecord
from atlas.safety.audit import AuditLog
from atlas.safety.classifier import TierClassifier
from atlas.safety.engine import SafetyEngine
from atlas.safety.killswitch import KillSwitch
from atlas.safety.manifest import Manifest
from atlas.safety.policy import KillSwitchPolicy, PolicyEngine


@dataclass
class SafetyComponents:
    classifier: TierClassifier
    safety: SafetyEngine
    cap_audit: Any  # async callable — typed as Any to avoid circular imports


def build_safety(
    *,
    config: AppConfig,
    manifest: Manifest,
    audit: AuditLog,
    killswitch: KillSwitch,
    clock: Clock,
    ids: IdGenerator,
) -> SafetyComponents:
    """Build safety layer. Returns classifier, engine, and audit callback."""
    classifier = TierClassifier(manifest, config.safety.default_tier_on_error)
    policy = PolicyEngine((KillSwitchPolicy(killswitch),))
    safety = SafetyEngine(
        classifier=classifier,
        policy=policy,
        audit=audit,
        killswitch=killswitch,
        clock=clock,
        cfg=config.safety,
    )

    async def cap_audit(**kw: Any) -> None:
        await audit.record(
            AuditRecord(
                correlation_id=kw["correlation_id"],
                ts=clock.now(),
                actor=kw["actor"],
                action=kw["action"],
                tool=kw.get("tool"),
                outcome=kw.get("outcome"),
                payload=kw.get("payload"),
            )
        )

    return SafetyComponents(classifier=classifier, safety=safety, cap_audit=cap_audit)
