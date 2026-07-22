# Requirements — Fix Stupid & General Bugs in ATLAS

## Background
After thorough code analysis, 6 concrete, stupid bugs were found in the ATLAS codebase. None of these require architectural changes — they are wrong imports, type mismatches, leaked dummy objects, misplaced imports, and mypy errors. Fixing them improves correctness, type safety, and startup reliability without touching any design decisions.

## Requirements

### REQ-1: Fix dummy IdentityPlatform leaking into NotificationPlatform
In `app.py`, a dummy `IdentityPlatform` is constructed with a fake hardcoded bytes key inside a `try/except` block and then passed to `build_notification_platform()`. The real `IdentityPlatform` (with the actual master key) is constructed ~50 lines later and stored as `identity_platform`, but `notification_platform` already received the dummy. The `Atlas` dataclass field `identity` is then correctly set to `identity_platform`, but `notification_platform` has an internal reference to the throwaway dummy object.

**Fix**: Remove the entire dummy-identity try/except block. Build the real `SecretStore` and `IdentityPlatform` early, before `build_notification_platform()` is called, so only one identity object ever exists. The `identity` field in `Atlas` and the one passed to `notification_platform` must be the same object.

### REQ-2: Fix `SecretStore` receiving bytes instead of str master key
In the dummy identity block in `app.py`, the `SecretStore` is called with `b"dummy_key_12345678901234567890123"` (bytes literal). But `SecretStore.__init__` takes `master_key: str` and calls `master_key.encode()` on it. Passing bytes causes an `AttributeError: 'bytes' object has no attribute 'encode'` at runtime if the try/except ever succeeds. This is fixed by removing the dummy block (REQ-1), but the type error should be noted.

### REQ-3: Move `import yaml` to module top-level in `app.py`
In `app.py`, `import yaml` appears mid-function inside `build()`, approximately at line 160. If `pyyaml` is not installed, this raises an `ImportError` after `db`, `safety`, `gateway`, and other objects have already been constructed with side effects (e.g., DB file created). Moving `import yaml` to the top of the file makes the failure fast and clean.

### REQ-4: Fix the 7 mypy errors in `tests/capabilities/test_dispatcher.py`
`mypy_errors.txt` shows exactly 7 errors, all in this one file:
- Lines 34, 42, 43, 51, 58, 60: "Unused `type: ignore` comment" — remove the `# type: ignore` comments that are no longer needed (mypy no longer flags those lines as errors)
- Line 46: `Item "None" of "Any | None" has no attribute "value"` — `res.payload` can be `None` at the type level; add a `assert res.payload is not None` guard before accessing `.value`

### REQ-5: Fix wrong module import in `tests/conftest.py`
`conftest.py` line: `from atlas.infra.logging import LoggingCfg, configure_logging  # type: ignore`

`LoggingCfg` lives in `atlas.infra.config`, not `atlas.infra.logging`. The `# type: ignore` exists precisely because the import is from the wrong module. Fix: import `LoggingCfg` from `atlas.infra.config` and remove the `# type: ignore`.

### REQ-6: Fix `NtfyNotifier.ask()` using deprecated `asyncio.get_event_loop()`
In `src/atlas/interfaces/notify.py`, `NtfyNotifier.ask()` calls `asyncio.get_event_loop()` to create a Future. In Python 3.10+, `get_event_loop()` in a context where no event loop is running emits a deprecation warning and in Python 3.12+ it may raise. The correct call is `asyncio.get_running_loop()`, which is always safe inside an `async def` method.

## Non-Goals
- No architectural changes
- No new features
- No changes to the Safety Engine logic, orchestration, or capability platforms
- No database schema changes
