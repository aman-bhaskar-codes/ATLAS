from pathlib import Path

import pytest

from atlas.tools.paths import PathError, resolve_in_allowlist


def test_resolve_in_allowlist(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    
    globs = [f"{allowed}/**"]
    
    # Valid
    rp = resolve_in_allowlist(str(allowed / "file.txt"), globs)
    assert rp.host == allowed / "file.txt"
    assert rp.mount_source == allowed
    assert rp.mount_target == "/work"
    assert rp.container == "/work/file.txt"
    
    # Traversal attack
    with pytest.raises(PathError):
        resolve_in_allowlist(str(allowed / "../outside.txt"), globs)
        
    # Unrelated
    with pytest.raises(PathError):
        resolve_in_allowlist("/tmp/evil", globs)
