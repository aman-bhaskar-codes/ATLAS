"""Computer-use engine — the single action loop for every substrate.

WHY one engine: browser, macOS and Android are different BODIES of one ATLAS.
The engine runs the same loop for all of them:

    perceive → resolve target → (safety gate happens at the tool layer) →
    execute → invalidate perception → re-perceive → verify

The engine NEVER talks to Playwright/AppleScript/ADB directly — only through
PerceptionAdapter/ControlAdapter. No substrate `if`-branches in this file.

Honesty rules enforced here (Phase 47):
* target not found in perception → fail, never guess coordinates
* action ran but expectation failed → verified=False, reported as such
* adapter missing/ unhealthy → honest limitation, not a fake attempt
"""

from __future__ import annotations

from dataclasses import dataclass

from atlas.capabilities.computer_use.cache import PerceptionCache
from atlas.capabilities.computer_use.telemetry import ComputerUseTelemetry
from atlas.capabilities.computer_use.verify import ExpectationSpec, VerificationResult, verify_snapshots
from atlas.control.contracts import ControlAction, ControlAdapter, ControlResult
from atlas.perception.contracts import PerceptionAdapter, PerceptionSnapshot, Substrate
from atlas.perception.targets import RESOLUTION_ORDER, ResolvedTarget, TargetRef, TargetStrategy

_ID_BASED = {TargetStrategy.STABLE_ID, TargetStrategy.RESOURCE_ID, TargetStrategy.ACCESSIBILITY_ID}


class SubstrateUnavailableError(RuntimeError):
    """Raised when a substrate has no adapter or its health check fails."""


@dataclass(frozen=True)
class ComputerUseOutcome:
    """Everything the loop produced — evidence, not vibes."""

    ok: bool
    substrate: Substrate
    operation: str
    result: ControlResult | None = None
    resolved: ResolvedTarget | None = None
    before: PerceptionSnapshot | None = None
    after: PerceptionSnapshot | None = None
    verification: VerificationResult | None = None
    note: str | None = None


def _surface(snapshot: PerceptionSnapshot) -> str:
    return snapshot.url or snapshot.app_name or snapshot.window_title or "default"


def resolve_target_in_snapshot(snapshot: PerceptionSnapshot, target: TargetRef) -> ResolvedTarget | None:
    """Resolve a universal target against perceived elements.

    Tries the requested strategy first, then degrades through RESOLUTION_ORDER
    using whatever target hints exist (text/label/semantic). Returns None when
    nothing matches — callers must fail honestly instead of guessing.
    """
    attempts: list[tuple[TargetStrategy, str]] = []
    if target.strategy in _ID_BASED or target.strategy in {TargetStrategy.ROLE, TargetStrategy.TEXT}:
        attempts.append((target.strategy, target.value))
    for hint in (target.text, target.label, target.semantic):
        if hint:
            attempts.append((TargetStrategy.TEXT, hint))
    if not attempts:
        return None
    seen: set[tuple[TargetStrategy, str]] = set()
    for strategy, value in attempts:
        if (strategy, value) in seen:
            continue
        seen.add((strategy, value))
        hit = _match(snapshot, strategy, value, target)
        if hit is not None:
            return hit
    return None


def _match(
    snapshot: PerceptionSnapshot, strategy: TargetStrategy, value: str, target: TargetRef
) -> ResolvedTarget | None:
    matches: list[tuple[int, float]] = []
    for idx, el in enumerate(snapshot.elements):
        if strategy in _ID_BASED:
            if el.stable_id and el.stable_id == value:
                matches.append((idx, 0.98))
        elif strategy is TargetStrategy.ROLE:
            if el.role == value and (not target.text or (el.label and target.text.lower() in el.label.lower())):
                matches.append((idx, 0.9))
        elif strategy is TargetStrategy.TEXT:
            if el.label and (el.label == value if target.exact else value.lower() in el.label.lower()):
                matches.append((idx, 0.88 if target.exact else 0.8))
    if not matches:
        return None
    if target.nth is not None:
        if target.nth >= len(matches):
            return None
        idx, conf = matches[target.nth]
    else:
        idx, conf = matches[0]
    el = snapshot.elements[idx]
    # Prefer the most reliable strategy actually used (RESOLUTION_ORDER rank).
    rank_bonus = max(0.0, (len(RESOLUTION_ORDER) - RESOLUTION_ORDER.index(strategy)) * 0.001)
    return ResolvedTarget(
        target=target,
        strategy_used=strategy,
        element_index=idx,
        stable_handle=el.stable_id,
        bounds=el.bounds,
        confidence=min(1.0, round(conf * snapshot.confidence + rank_bonus, 4)),
        evidence=f"matched {strategy.value}={value!r} against element role={el.role} label={el.label!r}",
    )


class ComputerUseEngine:
    """Substrate-neutral perception + action loop."""

    def __init__(
        self,
        perception: dict[Substrate, PerceptionAdapter],
        control: dict[Substrate, ControlAdapter],
        *,
        cache: PerceptionCache | None = None,
        telemetry: ComputerUseTelemetry | None = None,
    ) -> None:
        self._perception = perception
        self._control = control
        self._cache = cache or PerceptionCache()
        self._telemetry = telemetry or ComputerUseTelemetry()
        self._last_surface: dict[Substrate, str] = {}

    # --- health / introspection (Scenario 4: know your own limits) ---

    @property
    def telemetry(self) -> ComputerUseTelemetry:
        return self._telemetry

    def registered(self) -> dict[Substrate, dict[str, bool]]:
        return {
            s: {"perception": s in self._perception, "control": s in self._control}
            for s in Substrate
        }

    async def health(self) -> dict[Substrate, str]:
        out: dict[Substrate, str] = {}
        for substrate in Substrate:
            adapter = self._perception.get(substrate)
            if adapter is None:
                out[substrate] = "no adapter registered"
                continue
            status = await adapter.health()
            out[substrate] = "ok" if status.available else (status.permission_missing or status.detail)
        return out

    # --- perception ---

    async def perceive(self, substrate: Substrate, *, force: bool = False) -> PerceptionSnapshot:
        adapter = self._perception.get(substrate)
        if adapter is None:
            raise SubstrateUnavailableError(f"no perception adapter for {substrate.value}")
        if not force:
            cached = self._cache.get(substrate, self._last_surface.get(substrate, "default"))
            if cached is not None:
                return cached
        with self._telemetry.timed(substrate=substrate.value, operation="perceive") as ctx:
            snapshot = await adapter.snapshot()
            ctx["detail"] = f"elements={len(snapshot.elements)} confidence={snapshot.confidence}"
        self._last_surface[substrate] = _surface(snapshot)
        self._cache.put(substrate, self._last_surface[substrate], snapshot)
        return snapshot

    # --- action loop ---

    async def act(
        self,
        substrate: Substrate,
        action: ControlAction,
        expectations: tuple[ExpectationSpec, ...] = (),
    ) -> ComputerUseOutcome:
        adapter = self._control.get(substrate)
        if adapter is None:
            return ComputerUseOutcome(
                ok=False,
                substrate=substrate,
                operation=action.operation,
                note=f"no control adapter for {substrate.value} — capability unavailable on this machine",
            )
        # 1. perceive the world BEFORE acting (fresh for mutating ops)
        is_mutating = self._cache.invalidate_if_mutating(substrate, action.operation)
        try:
            before = await self.perceive(substrate, force=is_mutating)
        except SubstrateUnavailableError as exc:
            return ComputerUseOutcome(ok=False, substrate=substrate, operation=action.operation, note=str(exc))
        # 2. resolve target from evidence — never guess
        resolved: ResolvedTarget | None = None
        if action.target is not None:
            resolved = resolve_target_in_snapshot(before, action.target)
            if resolved is None:
                self._telemetry.bump_recovery()
                return ComputerUseOutcome(
                    ok=False,
                    substrate=substrate,
                    operation=action.operation,
                    before=before,
                    note=f"target not found in perception: {action.target.strategy.value}={action.target.value!r}",
                )
            self._telemetry.record(
                substrate=substrate.value,
                operation="resolve",
                latency_ms=0.0,
                ok=True,
                confidence=resolved.confidence,
                detail=resolved.evidence,
            )
        # 3. execute
        with self._telemetry.timed(substrate=substrate.value, operation=f"act:{action.operation}") as ctx:
            result = await adapter.dispatch(action)
            ctx["detail"] = result.evidence or result.error or ""
        # 4. re-perceive (cache is stale after mutation) + verify
        after: PerceptionSnapshot | None = None
        verification: VerificationResult | None = None
        if result.ok:
            self._cache.invalidate(substrate)
            try:
                after = await self.perceive(substrate, force=True)
            except SubstrateUnavailableError:
                after = None
            if expectations or is_mutating:
                if after is not None:
                    verification = verify_snapshots(expectations, before, after)
                else:
                    verification = VerificationResult(verified=False, evidence="none", detail="re-perception failed")
        ok = result.ok and (verification.verified if verification is not None else result.ok)
        return ComputerUseOutcome(
            ok=ok,
            substrate=substrate,
            operation=action.operation,
            result=result,
            resolved=resolved,
            before=before,
            after=after,
            verification=verification,
            note=None if result.ok else result.error,
        )

    # --- lifecycle ---

    async def shutdown(self) -> None:
        for adapter in self._control.values():
            try:
                await adapter.stop()
            except Exception:
                continue
