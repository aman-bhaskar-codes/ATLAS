"""Confidence calibration + uncertainty-driven behavior (Prompt 4 §33-§34).

ATLAS must learn whether its confidence is meaningful: predicted confidence
is paired with actual success, binned into a reliability curve, and reduced
to an expected calibration error. Persistent overconfidence feeds back into
escalation decisions (§33), and low confidence must change behavior — not
just a number in the UI (§34).
"""

from __future__ import annotations

from enum import StrEnum

from atlas.adaptation.domain import CalibrationBucket, CalibrationReport
from atlas.infra.clock import Clock, SystemClock
from atlas.infra.db import Database
from atlas.infra.logging import get_logger

_log = get_logger("atlas.adaptation.calibration")

#: Mean gap between confidence and realized success above which calibration
#: is considered poor enough to change escalation behavior (§33).
POOR_CALIBRATION_GAP = 0.15

DEFAULT_N_BUCKETS = 10


class UncertaintyAction(StrEnum):
    """Behavioral responses to low confidence (§34)."""

    GATHER_MORE_EVIDENCE = "GATHER_MORE_EVIDENCE"
    DEEPER_REASONING = "DEEPER_REASONING"
    ALTERNATE_MODEL = "ALTERNATE_MODEL"
    ALTERNATE_TOOL = "ALTERNATE_TOOL"
    VERIFY = "VERIFY"
    USER_CLARIFICATION = "USER_CLARIFICATION"


def uncertainty_actions(confidence: float) -> tuple[UncertaintyAction, ...]:
    """§34: low confidence changes behavior. Deterministic thresholds; the
    runtime consults this before acting on uncertain decisions."""
    if confidence < 0.3:
        return (
            UncertaintyAction.GATHER_MORE_EVIDENCE,
            UncertaintyAction.DEEPER_REASONING,
            UncertaintyAction.USER_CLARIFICATION,
        )
    if confidence < 0.5:
        return (
            UncertaintyAction.GATHER_MORE_EVIDENCE,
            UncertaintyAction.ALTERNATE_MODEL,
            UncertaintyAction.VERIFY,
        )
    if confidence < 0.7:
        return (UncertaintyAction.ALTERNATE_TOOL, UncertaintyAction.VERIFY)
    return ()


class CalibrationTracker:
    """Records (predicted confidence, actual success) pairs and reports
    calibration error + reliability curve (§33)."""

    def __init__(self, *, db: Database, clock: Clock | None = None) -> None:
        self._db = db
        self._clock = clock or SystemClock()

    async def record(self, predicted_confidence: float, actual_success: bool, *, trajectory_id: str = "") -> None:
        if not 0.0 <= predicted_confidence <= 1.0:
            msg = f"confidence must be in [0, 1], got {predicted_confidence}"
            raise ValueError(msg)
        await self._db.conn.execute(
            "INSERT INTO calibration_records (trajectory_id, predicted_confidence, actual_success, ts)"
            " VALUES (?,?,?,?)",
            (trajectory_id, predicted_confidence, int(actual_success), self._clock.now().isoformat()),
        )
        await self._db.conn.commit()

    async def report(self, *, n_buckets: int = DEFAULT_N_BUCKETS) -> CalibrationReport:
        cur = await self._db.conn.execute("SELECT predicted_confidence, actual_success FROM calibration_records")
        rows = await cur.fetchall()
        pairs = [(float(r["predicted_confidence"]), bool(r["actual_success"])) for r in rows]
        if not pairs:
            return CalibrationReport()

        buckets: list[CalibrationBucket] = []
        ece = 0.0
        n = len(pairs)
        for i in range(n_buckets):
            low, high = i / n_buckets, (i + 1) / n_buckets
            members = [p for p in pairs if low <= p[0] < high or (i == n_buckets - 1 and p[0] == high)]
            if not members:
                buckets.append(CalibrationBucket(index=i))
                continue
            mean_conf = sum(c for c, _ in members) / len(members)
            success_rate = sum(1 for _, s in members if s) / len(members)
            ece += len(members) / n * abs(mean_conf - success_rate)
            buckets.append(
                CalibrationBucket(index=i, n=len(members), mean_confidence=mean_conf, success_rate=success_rate)
            )
        report = CalibrationReport(n_records=n, calibration_error=ece, reliability_curve=tuple(buckets))
        _log.info("calibration.report", event_type="adaptation", n=n, ece=round(ece, 4))
        return report

    async def escalation_adjustment(self) -> str:
        """§33: how escalation should move given measured calibration.
        'raise' — overconfident, escalate more often; 'lower' —
        underconfident, can trust itself more; 'keep' — well calibrated or
        not enough evidence to say."""
        cur = await self._db.conn.execute(
            "SELECT AVG(predicted_confidence) AS conf, AVG(actual_success) AS success,"
            " COUNT(*) AS n FROM calibration_records"
        )
        row = await cur.fetchone()
        if row is None or int(row["n"]) < 10:
            return "keep"  # never adjust on thin evidence
        gap = float(row["conf"]) - float(row["success"])
        if gap > POOR_CALIBRATION_GAP:
            return "raise"
        if gap < -POOR_CALIBRATION_GAP:
            return "lower"
        return "keep"


__all__ = [
    "DEFAULT_N_BUCKETS",
    "POOR_CALIBRATION_GAP",
    "CalibrationTracker",
    "UncertaintyAction",
    "uncertainty_actions",
]
