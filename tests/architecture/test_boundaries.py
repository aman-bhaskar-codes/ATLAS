from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_import_boundaries() -> None:
    """Run import-linter to verify architecture boundaries.
    This test runs the same CLI command CI uses."""
    executable = Path(sys.executable).with_name("lint-imports")
    project_root = Path(__file__).resolve().parents[2]
    config = project_root / "importlinter.ini"
    res = subprocess.run(
        [str(executable), "--config", str(config)],
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"lint-imports failed:\n{res.stdout}\n{res.stderr}"
