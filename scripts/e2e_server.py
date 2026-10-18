"""Launch the real API with isolated state and no inherited service credentials."""

import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
state = Path(os.environ.get("ATLAS_E2E_DATA_DIR", str(root / ".e2e-data"))).resolve()
if not state.is_relative_to(root / ".e2e-data"):
    raise SystemExit("E2E state must stay inside .e2e-data")
state.mkdir(parents=True, exist_ok=True)
env = {name: os.environ[name] for name in ("PATH", "HOME", "TMPDIR", "SYSTEMROOT") if name in os.environ}
if "TMPDIR" not in env:
    env["TMPDIR"] = "/tmp"
env.update(
    ATLAS_ENV="dev",
    ATLAS_DATA_DIR=str(state),
    ATLAS_E2E_DATA_DIR=str(state),
    ATLAS_PROFILE="local_free",
    ATLAS_OLLAMA_HOST="http://127.0.0.1:1",
    ATLAS_RATE_LIMIT_CAPACITY="1000000",
    ATLAS_RATE_LIMIT_PER_MINUTE="1000000",
    ATLAS_DB_MMAP_SIZE="0",
    PYTHONUNBUFFERED="1",
)
# cwd contains no operator .env. Do not delete a prior test database implicitly.
os.chdir(state)
os.execve(
    sys.executable,
    [
        sys.executable,
        "-m",
        "uvicorn",
        "atlas.interfaces.api.app:create_app",
        "--factory",
        "--host",
        "127.0.0.1",
        "--port",
        "8730",
    ],
    env,
)
