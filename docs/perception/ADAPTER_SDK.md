# Adapter SDK — adding a new body to ATLAS

A new substrate (Windows UIA, Wayland, a robot arm, …) is exactly two classes
plus a bootstrap hook. The engine, safety, verification and telemetry come for
free.

## 1. Implement `PerceptionAdapter`

```python
from atlas.perception.contracts import (
    HealthStatus, PerceivedElement, PerceptionModality,
    PerceptionSnapshot, Substrate,
)

class MyPerceptionAdapter:
    substrate = Substrate.MY_BODY  # extend the Substrate enum first

    async def snapshot(self, target: object | None = None) -> PerceptionSnapshot:
        ...  # walk the substrate's accessibility/DOM/hierarchy

    async def health(self) -> HealthStatus:
        ...  # probe the substrate; NEVER raise — return available=False + detail

    async def capabilities(self) -> tuple[PerceptionModality, ...]:
        ...  # declare only what you really provide (DOM/ACCESSIBILITY/TEXT/VISION)

    # optional, on-demand only (keep snapshot() cheap):
    async def visual_evidence(self, *, full_page: bool = False): ...
```

Snapshot rules:

- one `PerceivedElement` per addressable thing: `role`, `label`, `value`,
  `enabled`, `focused`, `stable_id`, `bounds`, `confidence`
- expose STABLE identifiers whenever the substrate has them — they are the
  strongest resolution strategy
- bound the walk: cap element count and depth (see the browser adapter's
  `_flatten_ax(limit=200)`); perception must stay cheap
- set `sensitive=True` and a `note` when you degrade

## 2. Implement `ControlAdapter`

```python
from atlas.control.contracts import ControlAction, ControlResult

class MyControlAdapter:
    substrate = Substrate.MY_BODY

    async def dispatch(self, action: ControlAction) -> ControlResult: ...
    async def health(self) -> HealthStatus: ...
    async def capabilities(self) -> tuple[str, ...]: ...  # "click", "type", ...
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
```

Dispatch rules:

- translate `action.target` with `TargetRef` strategies — never invent
  coordinates; raise/return an error for strategies you cannot honor
- return `ControlResult(ok=False, error=...)` on ANY failure; never raise
- put what you observed AFTER executing into `evidence` (post-state URL,
  focused window, activity, …) — the audit trail depends on it
- reuse `correlation_id_of(action.arguments)` (`adapters/_cid.py`) so engine
  calls stay auditable end to end

## 3. Target strategies you must understand

`RESOLUTION_ORDER` (`src/atlas/perception/targets.py`) — strongest first:

```
stable_id → accessibility_id → role → semantic → dom_selector →
resource_id → xpath → text → image_ref → window_id → coordinates
```

The engine resolves the target against perception BEFORE dispatching. If it
cannot resolve, the action is refused — your adapter is never asked to guess.

## 4. Register in the bootstrap

`src/atlas/bootstrap/computer_use.py`: attach adapters only when
`EnvironmentReport.available(substrate)` is true. Add the probe to
`EnvironmentDetector` (`computer_use/environment.py`) so `atlas doctor` and
`atlas smoke-test` report the new body truthfully.

## 5. Tests

Mirror `tests/computer_use/`:

- adapter unit tests against a fake transport (see `fakes.py`)
- engine-level tests proving resolution, honest failure and verification
- an E2E against the real substrate where feasible (see `test_browser_e2e.py`;
  `pytest.skip` when the substrate is absent — honesty beats coverage)

## Checklist before merge

- [ ] `snapshot()` bounded (count + depth), `health()` never raises
- [ ] no coordinates used while a stable strategy exists
- [ ] `ControlResult.evidence` filled after every successful dispatch
- [ ] `capabilities()` lists only real operations
- [ ] ruff / mypy / lint-imports green; perception imports nothing from control
