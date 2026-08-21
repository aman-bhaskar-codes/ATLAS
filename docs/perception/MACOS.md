# macOS substrate

The macOS body combines two proven channels:

- **Perception** — Accessibility (AX) tree via PyObjC
  (`src/atlas/perception/macos_ax.py` → `MacOSAXBackend`)
- **Control** — AppleScript via `osascript`
  (`src/atlas/control/osascript.py`, script templates in
  `src/atlas/control/scripts.py`)

Adapters: `src/atlas/capabilities/computer_use/adapters/macos.py`

## Requirements

| Requirement | Why | Detection |
| --- | --- | --- |
| macOS | AppleScript/AX are platform APIs | `sys.platform == "darwin"` |
| PyObjC | AX tree walk | import probe |
| Accessibility permission | AX APIs return nothing without it | backend health probe |
| Automation permission | `osascript` driving System Events | granted by macOS on first use |

When AX is unavailable, `health()` returns
`HealthStatus(available=False, permission_missing="macos_accessibility", ...)`
— the engine and `atlas doctor` surface exactly that, never a fake snapshot.

## Perception

`MacOSPerceptionAdapter` converts the AX walk into the universal snapshot via
`snapshot_from_screen_state()` (`perception/contracts.py`): each `UIElement`
becomes a `PerceivedElement` with its `ax_path` as `stable_id` — the strongest
resolution strategy on macOS.

`window_title` / `app_name` are filled so `APP_ACTIVE` expectations and
surface-keyed caching work.

## Control

Operations (AppleScript rendered per action, executed through `OsascriptRunner`
with a timeout):

- `launch` — open an application
- `click` — click a UI element by accessible name inside an app
- `type_text` — type into the focused/target element
- `press_key` — keystrokes
- `close_window` — close the front window
- `open_file` — open a document with its default app

Target addressing uses the accessible **label** (`_target_label`): AX names
are more stable than coordinates. Coordinate taps are intentionally not part
of the macOS adapter — if an element has no accessible name, the honest answer
is "cannot resolve", not a pixel guess.

Arguments are escaped (`_escape`) before being interpolated into AppleScript
templates; every dispatch runs System Events through `tell application`.

## Verification patterns

- `APP_ACTIVE` after `launch`
- `TEXT_PRESENT` / `TEXT_ABSENT` after typing into editors (re-perceive AX)
- `STATE_CHANGED` for window/menu mutations

## Tests

`tests/computer_use/test_macos_adapter.py` exercises rendering and dispatch
against a scripted fake runner (deterministic, no real AppleScript needed).
On non-darwin CI machines the environment detector simply reports macOS as
unavailable — nothing is faked.
