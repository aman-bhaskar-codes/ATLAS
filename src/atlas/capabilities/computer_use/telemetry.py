"""Computer-use telemetry (Phase 36).

Records perception/grounding/action/verification latencies, adapter, target
confidence, outcome and recovery count. These are the raw material for the
future self-improvement system — and for the PERFORMANCE.md numbers, which
must be measured, not guessed (Phase 37).

In-memory ring buffer + structured logs: single-user scale, zero infra cost.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from pydantic import BaseModel

from atlas.infra.logging import get_logger

_log = get_logger("atlas.cu.telemetry")


class ComputerUseRecord(BaseModel):
    model_config = {"frozen": True}
    ts: float
    substrate: str
    operation: str  # perceive / resolve / act / verify
    latency_ms: float
    ok: bool
    confidence: float | None = None
    detail: str = ""


class ComputerUseTelemetry:
    def __init__(self, *, maxlen: int = 512) -> None:
        self._records: deque[ComputerUseRecord] = deque(maxlen=maxlen)
        self._recovery_count = 0

    def record(
        self,
        *,
        substrate: str,
        operation: str,
        latency_ms: float,
        ok: bool,
        confidence: float | None = None,
        detail: str = "",
    ) -> None:
        rec = ComputerUseRecord(
            ts=time.time(),
            substrate=substrate,
            operation=operation,
            latency_ms=round(latency_ms, 2),
            ok=ok,
            confidence=confidence,
            detail=detail,
        )
        self._records.append(rec)
        _log.debug(
            "cu.telemetry",
            event_type="computer_use",
            substrate=substrate,
            operation=operation,
            latency_ms=rec.latency_ms,
            ok=ok,
        )

    @contextmanager
    def timed(self, *, substrate: str, operation: str, confidence: float | None = None) -> Iterator[dict[str, Any]]:
        """Time a block; the caller sets ctx['ok'] / ctx['detail'] inside."""
        ctx: dict[str, Any] = {"ok": True, "detail": ""}
        start = time.perf_counter()
        try:
            yield ctx
        except Exception:
            ctx["ok"] = False
            raise
        finally:
            self.record(
                substrate=substrate,
                operation=operation,
                latency_ms=(time.perf_counter() - start) * 1000,
                ok=bool(ctx["ok"]),
                confidence=confidence,
                detail=str(ctx.get("detail", "")),
            )

    def bump_recovery(self) -> None:
        self._recovery_count += 1

    @property
    def recovery_count(self) -> int:
        return self._recovery_count

    def recent(self, limit: int = 50) -> list[ComputerUseRecord]:
        return list(self._records)[-limit:]

    def summary(self) -> dict[str, Any]:
        """Aggregate latencies per (substrate, operation) for PERFORMANCE.md."""
        buckets: dict[tuple[str, str], list[float]] = {}
        for rec in self._records:
            buckets.setdefault((rec.substrate, rec.operation), []).append(rec.latency_ms)
        out: dict[str, Any] = {}
        for (substrate, op), latencies in sorted(buckets.items()):
            out[f"{substrate}.{op}"] = {
                "count": len(latencies),
                "avg_ms": round(sum(latencies) / len(latencies), 2),
                "p95_ms": round(sorted(latencies)[int(len(latencies) * 0.95) if len(latencies) > 1 else 0], 2),
            }
        out["recovery_count"] = self._recovery_count
        return out
