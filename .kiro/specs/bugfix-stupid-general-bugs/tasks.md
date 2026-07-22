# Tasks — Fix Stupid & General Bugs in ATLAS

- [x] 1. Fix mid-function imports in app.py
  - Move `import yaml` from inside `build()` to the top-level import block in `src/atlas/app.py`
  - Move `from atlas.infra.types import Tier` (currently imported mid-function) to the existing top-level `from atlas.infra.types import AuditRecord` line — extend it to `from atlas.infra.types import AuditRecord, Tier`
  - Remove the now-duplicate mid-function import lines inside `build()` (keep local imports like `from atlas.capabilities.platforms.email_platform import EmailPlatform` that exist to avoid circular dependencies — leave those)
  - Run `uv run ruff check src/atlas/app.py` to verify clean
  - _Requirements: REQ-3_

- [x] 2. Fix dummy IdentityPlatform construction and object leak in app.py
  - In `src/atlas/app.py`, move the `cap_audit` async callback definition to right after `audit = AuditLog(db)` is created (it only closes over `audit` and `clock`, both available there)
  - Delete the entire dummy identity try/except block that constructs `IdentityPlatform` with `b"dummy_key_12345678901234567890123"` (bytes key) — this entire block must be removed
  - Build the real `master_key`, `secret_store`, and `identity_platform` right after `cap_audit` is defined and BEFORE `build_notification_platform()` is called
  - Pass `identity_platform` (not a dummy) to `build_notification_platform()`
  - Remove the duplicate `master_key = resolve_master_key(settings)`, `secret_store = SecretStore(db, master_key)`, and `identity_platform = IdentityPlatform(...)` lines that currently appear ~50 lines later in the function
  - Verify `return Atlas(...)` still uses `identity=identity_platform`
  - Verify `IdentityPlatform(` appears exactly once in the file after the fix
  - _Requirements: REQ-1, REQ-2_

- [x] 3. Fix mypy errors in tests/capabilities/test_dispatcher.py
  - Open `tests/capabilities/test_dispatcher.py` and remove the `# type: ignore` comment from lines 34, 42, 43, 51, 58, and 60 (these are stale unused ignores per mypy_errors.txt)
  - On the line that accesses `res.payload.value`, ensure there is an `assert res.payload is not None` guard immediately before it
  - Run `uv run mypy tests/capabilities/test_dispatcher.py` to confirm 0 errors
  - Run `uv run pytest tests/capabilities/test_dispatcher.py -v` to confirm all 3 tests pass
  - _Requirements: REQ-4_

- [x] 4. Fix wrong module import in tests/conftest.py
  - In `tests/conftest.py`, change `from atlas.infra.logging import LoggingCfg, configure_logging  # type: ignore` to two separate correct imports: `from atlas.infra.config import LoggingCfg` and `from atlas.infra.logging import configure_logging`
  - Remove the `# type: ignore` comment
  - Run `uv run pytest tests/ -q` to confirm all tests still pass
  - _Requirements: REQ-5_

- [x] 5. Fix deprecated asyncio.get_event_loop() in notify.py
  - In `src/atlas/interfaces/notify.py`, in `NtfyNotifier.ask()`, replace `asyncio.get_event_loop()` with `asyncio.get_running_loop()`
  - Run `uv run mypy src/atlas/interfaces/notify.py` to confirm clean
  - Run `uv run pytest tests/ -q` to confirm all tests pass
  - _Requirements: REQ-6_
