"""URL reputation checking domain models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ReputationVerdict(StrEnum):
    SAFE = "safe"
    MALICIOUS = "malicious"
    UNKNOWN = "unknown"


@dataclass
class ReputationResult:
    url: str
    verdict: ReputationVerdict
    reason: str
    checked_by: str
