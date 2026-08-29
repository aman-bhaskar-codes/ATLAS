"""WorkspaceEngine read-side tests — tree walk, versioning, and path safety.

Uses a real temp dir (tmp_path) rather than mocks: the engine's whole job is
faithful filesystem projection, so faking the FS would test nothing. The
invariants locked here are the ones later slices depend on:
  * the tree is ignore-aware and stamps files with a content hash `version`;
  * `read_document` returns a version equal to `hash_content(content)`;
  * a path escaping the root is refused (never served).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from atlas.capabilities.ide.workspace import (
    WorkspaceEngine,
    WorkspaceError,
    hash_content,
    open_workspace,
)


def _engine(tmp_path: Path) -> WorkspaceEngine:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hi')\n")
    (tmp_path / "README.md").write_text("# demo\n")
    # An ignored dir with a file that must NOT appear in the tree.
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "junk.js").write_text("x\n")
    return open_workspace("ws1", "demo", (str(tmp_path),), now_ts="2026-08-29T00:00:00Z")


class TestTree:
    def test_lists_files_and_dirs_ignoring_noise(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        paths = {n.path for n in eng.tree()}
        assert "src" in paths
        assert "src/app.py" in paths
        assert "README.md" in paths
        # node_modules and its contents are pruned entirely.
        assert not any(p.startswith("node_modules") for p in paths)

    def test_files_carry_version_dirs_do_not(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        by_path = {n.path: n for n in eng.tree()}
        app = by_path["src/app.py"]
        assert app.is_dir is False
        assert app.version == hash_content("print('hi')\n")
        assert app.language == "python"
        assert by_path["src"].is_dir is True
        assert by_path["src"].version is None  # dirs have no content hash


class TestReadDocument:
    def test_snapshot_version_matches_content_hash(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        snap, content = eng.read_document("src/app.py")
        assert content == "print('hi')\n"
        assert snap.version == hash_content(content)
        assert snap.path == "src/app.py"
        assert snap.language == "python"
        assert snap.line_count == 1

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        with pytest.raises(WorkspaceError):
            eng.read_document("src/nope.py")


class TestPathSafety:
    def test_escape_via_dotdot_is_refused(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        with pytest.raises(WorkspaceError):
            eng.read_document("../secrets.env")

    def test_absolute_path_is_refused(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        with pytest.raises(WorkspaceError):
            eng.read_document("/etc/passwd")


class TestConstruction:
    def test_missing_root_dir_raises(self, tmp_path: Path) -> None:
        with pytest.raises(WorkspaceError):
            open_workspace("ws1", "demo", (str(tmp_path / "does-not-exist"),), now_ts="t")

    def test_no_root_paths_raises(self, tmp_path: Path) -> None:
        with pytest.raises(WorkspaceError):
            open_workspace("ws1", "demo", (), now_ts="t")
