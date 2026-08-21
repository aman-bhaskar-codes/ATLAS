# Browser substrate

The browser body reuses the mature `atlas.capabilities.browser` platform
(sessions, pages, engines, safety tiers) and exposes it through the universal
contracts.

Code: `src/atlas/capabilities/computer_use/adapters/browser.py`

## One shared context

Perception and control share ONE `BrowserContext`, so "perceive" and "act"
always observe/mutate the SAME page:

```
BrowserContext(platform: BrowserPlatform)
    ensure_page()    → creates session + page lazily
    current_page()   → PageHandle | None
    close()          → closes the session
```

Bootstrap (`build_computer_use`) attaches the adapters only when the
environment report says browser is available AND a `BrowserPlatform` was
constructed (`config.browser.enabled` + `build_browser_platform(...)` in
`src/atlas/app.py`).

## Perception: DOM + accessibility, never raw HTML

`BrowserPerceptionAdapter.snapshot()` builds `PageState` via
`platform.build_state(handle)` and flattens two channels into
`PerceivedElement`s:

1. **accessibility slice** — roles/names/values from the AX tree
   (`_flatten_ax`, capped at 200 elements)
2. **visible interactable elements** — tag, text, `id` (becomes `stable_id`),
   bounding box, selected attributes (`href/type/name/aria-label`)

The raw DOM never leaves the adapter. Snapshot state carries
`loading`, `dom_hash` (cheap change detection) and `auth` status. Confidence
drops to 0.5 while the page is still loading.

Screenshots are NOT part of `snapshot()` — `visual_evidence(full_page=...)`
captures on demand only, keeping perception cheap.

## Control: operations and addressing

Supported operations: `navigate`, `back`, `forward`, `reload`, `click`,
`type_text`.

`target_to_locator()` is the ONLY place substrate addressing happens:

| TargetStrategy | Locator |
| --- | --- |
| `dom_selector` | CSS |
| `xpath` | XPATH |
| `stable_id` | CSS `[id="..."]` |
| `text` / `semantic` | TEXT (accessible/visible text) |
| `role` / `accessibility_id` | ROLE (+ accessible name) |
| fallback | LABEL |
| `image_ref` / `coordinates` | **rejected** — the browser never needs pixel taps |

Every dispatch carries a correlation id (`arguments["correlation_id"]`, see
`adapters/_cid.py`) into the platform engines, keeping the audit trail intact.

## Verification patterns

Useful `ExpectationSpec`s for browser actions:

- `URL_CONTAINS` after navigate/back/forward
- `TEXT_PRESENT` / `TEXT_ABSENT` after clicks that mutate page content
- `ELEMENT_PRESENT(role=..., value=...)` for new widgets
- `STATE_CHANGED` when only `dom_hash`/element count can prove the change

`ControlResult.evidence` after browser actions: `url=... title=... dom_hash=...`.

## End-to-end proof

`tests/computer_use/test_browser_e2e.py` drives real headless chromium over a
local HTML fixture through the full engine loop:

```
navigate → perceive → type (role target) → click (stable-id target)
         → verify "Hello, ATLAS!" text evidence → PNG visual evidence
```

plus the honesty case: a nonexistent target yields `target not found in
perception`, nothing dispatched, no verification claimed. The test skips when
chromium is unavailable instead of faking.

## Known limits

- Playwright ≥ 1.40 removed `page.accessibility`; the E2E platform parses
  `page.aria_snapshot()` YAML instead.
- `file://` URLs are exercised by the test platform; the production
  `BrowserPlatform` additionally applies URL-reputation/safe-browsing tiers.
