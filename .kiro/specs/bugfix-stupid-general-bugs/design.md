# Design — Fix Stupid & General Bugs in ATLAS

## Overview
Six targeted fixes across four files. Each fix is a surgical edit — no new abstractions introduced, no interfaces changed.

## Fix 1 & 2 — `app.py`: Remove dummy identity, build real identity early

**Current broken flow:**
```
build():
  db = Database(...)                          # side effect: DB file created
  safety = SafetyEngine(...)
  
  try:                                        # BUG: dummy leaks into notification
    identity = IdentityPlatform(store=SecretStore(db, b"dummy_key..."), ...)  # bytes key!
  except:
    identity = None
    
  notification_platform = build_notification_platform(..., identity=identity)  # gets dummy
  
  ...~50 lines later...
  master_key = resolve_master_key(settings)   # real key
  secret_store = SecretStore(db, master_key)  # real store
  identity_platform = IdentityPlatform(...)   # real identity
  
  return Atlas(..., identity=identity_platform)  # correct field
  # BUT notification_platform has the dummy identity internally!
```

**Fixed flow:**
```
build():
  db = Database(...)
  safety = SafetyEngine(...)
  
  # Build real identity ONCE, EARLY — before notification_platform
  master_key = resolve_master_key(settings)
  secret_store = SecretStore(db, master_key)
  identity_platform = IdentityPlatform(
      store=secret_store, db=db,
      strategies={
          CredentialKind.API_KEY: ApiKeyStrategy(),
          CredentialKind.JWT: JwtStrategy(),
          CredentialKind.BROWSER_SESSION: BrowserSessionStrategy(),
      },
      audit=cap_audit,   # NOTE: cap_audit is defined before this in the fixed order
  )
  
  notification_platform = build_notification_platform(..., identity=identity_platform)
  
  # Remove the duplicate secret_store / identity_platform construction that was here
  return Atlas(..., identity=identity_platform)
```

**Ordering issue**: `cap_audit` callback currently depends on `audit` which is built before the identity block, so that ordering is fine. `cap_audit` itself is currently defined after `cap_dispatcher`, but we need it before `identity_platform`. Solution: move `cap_audit` definition up to right after `audit` is created (it only closes over `audit` and `clock`, both available early).

## Fix 3 — `app.py`: Move `import yaml` to module top

Move `import yaml` from inside `build()` to the top of `app.py` with the other standard imports. Also move `from atlas.infra.types import Tier` (currently also imported mid-function) to the top-level `from atlas.infra.types import ...` line (it already imports `AuditRecord` from there; add `Tier` to that same import).

## Fix 4 — `tests/capabilities/test_dispatcher.py`: Fix mypy errors

- Remove the 6 `# type: ignore` comments on lines 34, 42, 43, 51, 58, 60
- On line 46 (the `assert res.payload is not None` fix), add the assertion before `res.payload.value` is accessed:
  ```python
  assert res.ok
  assert res.payload is not None   # add this line
  assert res.payload.value == "hi" and res.provider == "fake"
  ```

## Fix 5 — `tests/conftest.py`: Fix wrong import module

```python
# Before (wrong module):
from atlas.infra.logging import LoggingCfg, configure_logging  # type: ignore

# After (correct):
from atlas.infra.config import LoggingCfg
from atlas.infra.logging import configure_logging
```

## Fix 6 — `src/atlas/interfaces/notify.py`: Replace deprecated `get_event_loop()`

```python
# Before:
loop = asyncio.get_event_loop()
fut: asyncio.Future[bool] = loop.create_future()

# After:
loop = asyncio.get_running_loop()
fut: asyncio.Future[bool] = loop.create_future()
```

## Files Changed
1. `src/atlas/app.py` — Fix 1, 2, 3
2. `tests/capabilities/test_dispatcher.py` — Fix 4
3. `tests/conftest.py` — Fix 5
4. `src/atlas/interfaces/notify.py` — Fix 6

## Verification
After all fixes:
- `uv run mypy src/ --strict` should show 0 errors (was 7 in `mypy_errors.txt`, all in test file)
- `uv run pytest tests/` should pass (78+ tests)
- `uv run ruff check src/ tests/` should be clean
- `uv run atlas doctor` should complete startup without errors (requires Ollama)
