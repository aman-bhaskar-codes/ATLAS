"""Project intelligence — `analyze_project` against fixture repos (Phase 10-11).

Pure, offline: builds a fake repo on tmp_path, opens a real `WorkspaceEngine`, and
asserts the derived `ProjectModel`. Locks the detection contract the agentic loop
depends on — languages, package manager, frameworks, and the test/build/run
COMMANDS (candidates, never executed here). Also pins the incremental-fingerprint
invariant: a source edit does not move it; a manifest change does.
"""

from __future__ import annotations

import json
from pathlib import Path

from atlas.capabilities.ide.contracts import WorkspaceId, WorkspaceRef
from atlas.capabilities.ide.project import analyze_project
from atlas.capabilities.ide.workspace import WorkspaceEngine

_TS = "2026-08-29T00:00:00+00:00"


def _engine(root: Path) -> WorkspaceEngine:
    ref = WorkspaceRef(id=WorkspaceId("w1"), name="demo", root_paths=(str(root),), created_ts=_TS, last_opened_ts=_TS)
    return WorkspaceEngine(ref)


def _python_repo(root: Path) -> None:
    (root / "pyproject.toml").write_text(
        '[project]\nname = "demo"\ndependencies = ["fastapi>=0.100", "pydantic~=2.0"]\n\n[tool.uv]\n'
    )
    (root / "conftest.py").write_text("")
    (root / "main.py").write_text("print('hi')\n")
    (root / "mod.py").write_text("x = 1\n")


def _node_repo(root: Path) -> None:
    (root / "package.json").write_text(
        json.dumps(
            {
                "name": "web",
                "dependencies": {"next": "^14", "react": "^18"},
                "devDependencies": {"vitest": "^1"},
                "scripts": {"test": "vitest", "build": "next build", "dev": "next dev"},
            }
        )
    )
    (root / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n")
    (root / "src").mkdir()
    (root / "src" / "index.ts").write_text("export const x = 1\n")


class TestPython:
    def test_detects_python_stack(self, tmp_path: Path) -> None:
        _python_repo(tmp_path)
        pm = analyze_project(_engine(tmp_path))
        assert pm.languages[0] == "python"
        assert "uv" in pm.package_managers
        assert "fastapi" in pm.frameworks and "pydantic" in pm.frameworks
        assert "pytest" in pm.test_commands  # conftest.py present
        assert "python -m build" in pm.build_commands
        assert "main.py" in pm.entrypoints
        assert "fastapi" in pm.dependencies
        assert pm.fingerprint  # a manifest was seen


class TestNode:
    def test_detects_node_stack_and_scripts(self, tmp_path: Path) -> None:
        _node_repo(tmp_path)
        pm = analyze_project(_engine(tmp_path))
        assert "typescript" in pm.languages
        assert pm.package_managers == ("pnpm",)  # from pnpm-lock.yaml
        assert "nextjs" in pm.frameworks and "react" in pm.frameworks
        # pnpm commands omit the `run` npm needs.
        assert "pnpm test" in pm.test_commands
        assert "pnpm build" in pm.build_commands
        assert "pnpm dev" in pm.run_commands
        assert "src/index.ts" in pm.entrypoints


class TestRustGo:
    def test_rust(self, tmp_path: Path) -> None:
        (tmp_path / "Cargo.toml").write_text('[package]\nname = "x"\n')
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.rs").write_text("fn main() {}\n")
        pm = analyze_project(_engine(tmp_path))
        assert pm.package_managers == ("cargo",)
        assert pm.test_commands == ("cargo test",)
        assert "src/main.rs" in pm.entrypoints

    def test_go(self, tmp_path: Path) -> None:
        (tmp_path / "go.mod").write_text("module demo\n\ngo 1.22\n")
        (tmp_path / "main.go").write_text("package main\nfunc main() {}\n")
        pm = analyze_project(_engine(tmp_path))
        assert pm.package_managers == ("go",)
        assert "go test ./..." in pm.test_commands


class TestUnknownAndFingerprint:
    def test_unknown_stack_is_partial_not_error(self, tmp_path: Path) -> None:
        (tmp_path / "notes.txt").write_text("hello\n")
        pm = analyze_project(_engine(tmp_path))
        assert pm.package_managers == () and pm.test_commands == ()
        assert pm.fingerprint == ""  # no manifest → no fingerprint
        assert pm.file_count == 1

    def test_fingerprint_stable_across_source_edit(self, tmp_path: Path) -> None:
        _python_repo(tmp_path)
        first = analyze_project(_engine(tmp_path)).fingerprint
        (tmp_path / "mod.py").write_text("x = 2  # edited source, not a manifest\n")
        assert analyze_project(_engine(tmp_path)).fingerprint == first
        # But changing the manifest DOES move it.
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\ndependencies = ["flask"]\n')
        assert analyze_project(_engine(tmp_path)).fingerprint != first
