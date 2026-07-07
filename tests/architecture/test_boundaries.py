from __future__ import annotations

import subprocess


def test_import_boundaries() -> None:
    """Run import-linter to verify architecture boundaries.
    This test runs the same CLI command CI uses."""
    res = subprocess.run(["import-linter"], capture_output=True, text=True)
    assert res.returncode == 0, f"import-linter failed:\n{res.stdout}\n{res.stderr}"
