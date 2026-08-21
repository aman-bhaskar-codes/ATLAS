# Perception & Computer-Use Architecture

> ONE MIND · MANY SENSES · MANY HANDS · ONE SAFETY BOUNDARY

ATLAS has a single cognitive core. It observes the world through **universal
perception contracts** and acts through **universal control contracts**. The
substrate — browser, macOS, Android, a public HTTP API — is a *body*, and
bodies are swappable without touching the mind.

## Layer map

Import direction is enforced by `importlinter.ini` (contract kept in CI):

```
atlas.interfaces            # tool/API surface
atlas.capabilities          # computer_use, browser, public_api, ...
atlas.control               # universal control contracts + osascript
atlas.perception            # universal perception contracts + backends
atlas.infra                 # ids, types, logging, cognition primitives
```

`atlas.control` sits ABOVE `atlas.perception`: control contracts may compose
perception primitives (`Substrate`, `TargetRef`, `HealthStatus`); perception
imports nothing from control.

## The two universal contracts

| Contract | File | What every body implements |
| --- | --- | --- |
| `PerceptionAdapter` | `src/atlas/perception/contracts.py` | `substrate`, `snapshot()`, `health()`, `capabilities()` (+ optional `visual_evidence()`) |
| `ControlAdapter` | `src/atlas/control/contracts.py` | `substrate`, `dispatch()`, `health()`, `capabilities()`, `start()`, `stop()` |

The core speaks only `PerceptionSnapshot`, `ControlAction`, `TargetRef` and
`ControlResult`. No `if mac:` / `if android:` branch exists outside adapters.

## The action loop (one engine, every substrate)

`ComputerUseEngine` (`src/atlas/capabilities/computer_use/engine.py`):

```
perceive → resolve target from evidence → dispatch → invalidate perception
        → re-perceive → verify against declared expectations
```

Honesty rules enforced inside the loop (Phase 47):

- target not found in perception → **fail honestly**, never guess coordinates
- `ControlResult.ok` means the primitive ran, **not** that the goal was met
- verification only claims `verified=True` when post-action perception proves
  every `ExpectationSpec`; with no expectations it refuses to claim anything

## Module map

```
src/atlas/perception/          contracts, targets, AX backend, fusion, sensitivity
src/atlas/control/             contracts, osascript runner + scripts, legacy tool
src/atlas/capabilities/computer_use/
    engine.py                  the substrate-neutral loop
    adapters/browser.py        BrowserPlatform ⇄ universal contracts
    adapters/macos.py          AX perception ⇄ AppleScript control
    adapters/android.py        ADB perception/control (+ android_transport.py)
    verify.py                  ExpectationSpec / verify_snapshots
    cache.py                   bounded perception cache (TTL + LRU)
    redaction.py               sensitive-text redaction for snapshots
    telemetry.py               latency / confidence / recovery counters
    environment.py             EnvironmentDetector (what bodies really exist)
    vision.py                  on-demand visual evidence
    tool.py                    ComputerUseTool — everything flows through SafetyEngine
src/atlas/capabilities/public_api/   catalog → retrieval → validation → execution
src/atlas/bootstrap/computer_use.py  attach bodies based on REAL detection
```

## Bodies are environment facts, not config

`EnvironmentDetector` probes the machine (osascript/AX on macOS, Playwright
for browser, `adb devices` for Android, network for APIs). `build_computer_use`
attaches adapters only for what exists. A missing body is a normal state:
`engine.health()` and `explain_limitation()` report it instead of faking
attempts. See `atlas doctor` and `atlas smoke-test`.

## Evidence, not vibes

Every outcome is a `ComputerUseOutcome`: before/after snapshots, the resolved
target with its evidence chain, the adapter result, and the verification
verdict. Anything reported to the user or the model is traceable to one of
these artifacts.
