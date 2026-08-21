# ATLAS — Prompt 2/5 Final Report

**Universal Perception, Computer Use, Control Fabric & Capability Discovery**
Status: **COMPLETE — all Phase 52 quality gates green**

> ONE MIND · MANY SENSES · MANY HANDS · ONE SAFETY BOUNDARY

---

## 1. What was built

### Universal contracts (Phases 1–6)
- `atlas.perception` — `PerceptionAdapter` protocol, `PerceptionSnapshot`,
  `PerceivedElement`, `HealthStatus`, `TargetRef`/`ResolvedTarget` with the
  11-strategy `RESOLUTION_ORDER` (stable ids first, coordinates last)
- `atlas.control` — `ControlAdapter` protocol, `ControlAction` (with
  confidence/evidence/risk_hint/reversible), `ControlResult`
- Layering fixed and enforced: `control` above `perception` in
  `importlinter.ini`; perception imports nothing from control

### Substrate adapters (Phases 7–11)
- **browser** — `BrowserContext` shared by perception+control over the mature
  `BrowserPlatform`; DOM+AX snapshots; `target_to_locator` is the only
  addressing point; coordinates rejected
- **macOS** — AX perception (`MacOSAXBackend`) + AppleScript control
  (`OsascriptRunner`); honest `permission_missing="macos_accessibility"`
- **Android** — uiautomator-dump perception + `input`/`am` control behind an
  `AndroidTransport` protocol; coordinates only as last-resort from real bounds

### The loop (Phases 12–21)
- `ComputerUseEngine` — one substrate-neutral loop: perceive → resolve →
  dispatch → invalidate → re-perceive → verify. No substrate `if`-branches.
- `verify.py` — 6 expectation kinds; no expectations ⇒ refuses to claim
  verification (Phase 47)
- `cache.py` (TTL+LRU, mutating-op invalidation), `redaction.py`,
  `telemetry.py` (p95/ok-rate/recovery counters), `environment.py`
  (real detection of every body), `vision.py` (honest `NullVisionGrounder`)
- `ComputerUseTool` — everything flows through the SAME `SafetyEngine.guard()`
  as filesystem/shell/email; explicit `SideEffect` records

### Capability discovery (Phases 22–29)
- `atlas.capabilities.public_api` — 14-API bundled catalog, intent retrieval
  (free-first/no-key bonuses), lifecycle registry
  (`DISCOVERED→…→VALIDATED/AVAILABLE`, evidence required for promotion),
  HTTPS+keyless bounded-GET validation, bounded untrusted provenanced
  execution; `ConnectorNotExecutableError` for anything not executable

### Environment, CLI, docs (Phases 30–40, 50)
- `build_computer_use()` bootstrap attaches only bodies that really exist
- `atlas doctor` + new `atlas smoke-test` (substrate table, catalog seed,
  discovery, safety-refusal check)
- 9 docs in `docs/perception/`: ARCHITECTURE, ADAPTER_SDK, BROWSER, MACOS,
  ANDROID, VISION, PUBLIC_APIS, SAFETY, PERFORMANCE

## 2. Verification — Phase 52 gates (all green)

| Gate | Result |
| --- | --- |
| `uv run pytest` | **513 passed / 0 failed / 0 errors / 0 skipped** |
| `uv run ruff check .` | All checks passed |
| `uv run mypy` (strict) | Success: no issues in 404 source files |
| `uv run lint-imports` | Contracts: 3 kept, 0 broken |
| `atlas doctor` | runs (Ollama-not-running reported as environment fact) |
| `atlas smoke-test` | SMOKE TEST PASSED |
| Browser E2E (real chromium) | 2/2 passed (~3 s) |

Test distribution for this prompt's areas: perception 3, control 5,
computer_use 55 (incl. real-browser E2E), connectors 22.

## 3. Acceptance scenarios (Phase 51)

1. **GitHub browser inspect** — covered by the chromium E2E loop
   (navigate→perceive→act→verify) on a local fixture; network-free by design
2. **macOS text editor** — adapter unit tests + honest permission reporting;
   live AX run requires user-granted Accessibility permission
3. **Android Settings tap** — adapter tests over scripted ADB transport;
   live run requires adb + device (environment reports absence honestly)
4. **Capability-limitation explanation** — `explain_limitation()` /
   `engine.health()` tested; smoke-test shows `○ adb not installed`
5. **Weather via validated provider** — connectors E2E: validate → execute →
   untrusted + provenanced data
6. **Unknown API stays DISCOVERED/UNVALIDATED** — execution refused with
   "DISCOVERED/UNVALIDATED — execution refused", zero network calls

## 4. Honest limitations (what was NOT faked)

- No live GitHub/Android device runs in CI — deterministic stand-ins +
  skip-on-absence instead of simulations
- Vision grounding is a seam (`NullVisionGrounder`), not an implementation
- macOS E2E requires the operator's Accessibility permission grant
- Concurrent uncommitted cognitive-runtime work on the same working tree was
  left untouched except two test-mock fixes required to keep the suite green

## 5. Next exact implementation priorities for Prompt 3

1. **Live-substrate E2E harness** — opt-in CI job running the macOS AX path
   (permission-granted runner) and an Android emulator (`adb` + uiautomator),
   reusing `test_browser_e2e.py` patterns
2. **Real VisionGrounder backend** — implement the `VisionGrounder` protocol
   over a local/free model; gate coordinate strategies on
   `available() == True`; add grounding E2E on synthetic screenshots
3. **Orchestrator integration** — teach the planner/response-parser to emit
   `computer_use` tool calls with expectations, and feed `verified`/`evidence`
   back into reasoning context (closes the mind→hands loop end-to-end)
4. **Connector growth** — keyed-API approval flow (credential vault binding)
   so GitHub-class APIs can reach `AVAILABLE` with user consent; catalog
   expansion beyond 14 entries
5. **Trace-based learning** — persist verified `ComputerUseOutcome` chains to
   episodic memory as reusable skills (perceive→act→verify recipes)
6. **Frontend trust surface** — live computer-use telemetry panel
   (p95, ok-rate, recovery count, evidence chain per correlation id)
7. **Multi-device fan-out** — `ADBTransport(serial=...)` already supports it;
   add device selection to `EnvironmentReport` + engine routing

---

*Delivered per Prompt 2/5 spec. Philosophy held: ATLAS never claims what it
cannot prove.*
