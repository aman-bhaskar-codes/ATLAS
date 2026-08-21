# Android substrate

The Android body is built on **ADB + uiautomator** — no app install, no root,
no vendor SDK. Everything travels over `adb shell`.

Code:

- adapters — `src/atlas/capabilities/computer_use/adapters/android.py`
- transport — `src/atlas/capabilities/computer_use/adapters/android_transport.py`

## Requirements

| Requirement | Detection |
| --- | --- |
| `adb` on PATH | `shutil.which("adb")` |
| one connected device | `adb devices` probe (`EnvironmentDetector` lists serials; bootstrap uses the first) |
| USB debugging enabled on device | implied by a successful connection |

If adb is missing (e.g. a laptop without the Android SDK), the environment
report marks android unavailable and `atlas smoke-test` shows `○ adb not
installed` — the substrate is simply absent, never simulated.

## Perception: uiautomator dump

```
uiautomator dump /sdcard/atlas_ui.xml && cat /sdcard/atlas_ui.xml
```

`parse_uiautomator_dump()` (pure function, unit-tested) turns the XML into
flat nodes with:

- `resource-id` → `stable_id` (strongest strategy on Android)
- `content-desc` / `text` → labels
- `bounds` → `(x, y, w, h)`
- `class` → role

Snapshot `source="uiautomator"`, modalities `STRUCTURE + TEXT + APP_STATE`.

## Control

Operations: `launch` (am start), `tap`, `long_press`, `swipe` (input),
`type_text` (input text), `press_key` (keyevent).

Target resolution inside the adapter (`_find_node`) prefers:

1. `resource_id` match
2. `accessibility_id` (content-desc) match
3. `text` / label match

and only then falls back to the node's bounds center for `input tap x y` —
coordinates are a LAST RESORT derived from a real perceived element, never
invented.

## Transport abstraction

`AndroidTransport` is a protocol (`shell(command, timeout_s)`,
`is_connected()`). Production uses `ADBTransport` (async subprocess, per-call
timeouts, "adb not found" surfaced as a transport error). Tests use a
scripted fake — see `tests/computer_use/test_android_adapter.py`.

## Verification patterns

- `APP_ACTIVE` after `launch` (activity check)
- `TEXT_PRESENT` after taps that change screens
- `STATE_CHANGED` for list/scroll mutations

## Honest limits

- uiautomator dumps are point-in-time; rapid animations may require retry
- some surfaces (games, WebView internals) expose little structure — the
  snapshot then carries few elements and target resolution honestly fails
- devices with USB debugging disabled are unreachable; detection reports it
