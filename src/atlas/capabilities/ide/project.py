"""Project intelligence — build a `ProjectModel` by reading the workspace (Phase 10-11).

WHAT: a PURE, read-only analysis that turns a workspace root into the structured
`ProjectModel` an agent reasons about instead of dumping the whole tree into a
prompt (Constitution: never blast the repo at the LLM). It answers the questions
the agentic loop opens with — what languages, which package manager, which
frameworks, how do I test/build/run this, what are the entrypoints.

WHY here (capabilities/ide) and read-only: detection is pure filesystem reads of
manifest files (`pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`, …) plus
the extension histogram of the tree the `WorkspaceEngine` already walked. No
subprocess, no network, no writes — Tier-0, so no safety funnel is involved. The
commands it reports are *candidates* to run later through the governed terminal
(a separate slice), never executed here.

WHY a `fingerprint`: the manifests + their content hashes are folded into one
cheap sha256. Re-analysis is incremental — if the fingerprint is unchanged the
caller can reuse the cached model rather than re-scan. Editing a source file does
not change it; changing a dependency manifest does.

Detection is intentionally heuristic and best-effort: an unknown stack yields a
partial model (languages from extensions, empty commands) rather than an error —
an honest "I could not determine the build command" beats a fabricated one.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from atlas.capabilities.ide.contracts import ProjectModel
from atlas.capabilities.ide.workspace import WorkspaceEngine, hash_content
from atlas.infra.logging import get_logger

_log = get_logger("atlas.ide.project")

# Extension → language, for the tree histogram. Kept small and coarse: the goal
# is "which languages dominate", not exhaustive classification.
_LANG_BY_EXT: dict[str, str] = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".kt": "kotlin",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".c": "c",
    ".swift": "swift",
    ".sh": "shell",
}

# Node dependency name → framework label. First match wins; order is significance.
_NODE_FRAMEWORKS: tuple[tuple[str, str], ...] = (
    ("next", "nextjs"),
    ("nuxt", "nuxt"),
    ("@angular/core", "angular"),
    ("svelte", "svelte"),
    ("vue", "vue"),
    ("react", "react"),
    ("vite", "vite"),
    ("express", "express"),
    ("fastify", "fastify"),
    ("@nestjs/core", "nestjs"),
)

# Python dependency (normalised, lowercase) → framework label.
_PY_FRAMEWORKS: tuple[tuple[str, str], ...] = (
    ("fastapi", "fastapi"),
    ("django", "django"),
    ("flask", "flask"),
    ("starlette", "starlette"),
    ("pydantic", "pydantic"),
    ("torch", "pytorch"),
    ("tensorflow", "tensorflow"),
)

# Common entrypoint files to probe for (workspace-relative, posix).
_ENTRYPOINT_CANDIDATES: tuple[str, ...] = (
    "main.py",
    "app.py",
    "manage.py",
    "src/main.py",
    "src/index.ts",
    "src/index.js",
    "src/main.ts",
    "index.js",
    "index.ts",
    "cmd/main.go",
    "main.go",
    "src/main.rs",
)

_MAX_DEPS = 100  # bound the dependency list so a monorepo lockfile can't explode it


def _norm_pkg(name: str) -> str:
    """A dependency spec ('fastapi>=0.100', 'react@^18') → its bare package name."""
    return re.split(r"[<>=!~\[\]@;\s]", name.strip(), maxsplit=1)[0].strip().lower()


def analyze_project(engine: WorkspaceEngine, *, max_tree_entries: int = 20_000) -> ProjectModel:
    """Derive a `ProjectModel` from the workspace root. Pure + best-effort: an
    unrecognised stack yields a partial model, never an exception."""
    root = engine.root
    nodes = engine.tree(max_entries=max_tree_entries)

    languages: dict[str, int] = {}
    file_count = 0
    for node in nodes:
        if node.is_dir:
            continue
        file_count += 1
        lang = _LANG_BY_EXT.get(Path(node.path).suffix.lower())
        if lang is not None:
            languages[lang] = languages.get(lang, 0) + 1

    package_managers: list[str] = []
    frameworks: list[str] = []
    test_commands: list[str] = []
    build_commands: list[str] = []
    run_commands: list[str] = []
    dependencies: list[str] = []
    fingerprint_parts: list[str] = []

    # ── Python ──────────────────────────────────────────────────────────
    _detect_python(root, package_managers, frameworks, test_commands, build_commands, dependencies, fingerprint_parts)
    # ── Node / JS-TS ────────────────────────────────────────────────────
    _detect_node(
        root,
        package_managers,
        frameworks,
        test_commands,
        build_commands,
        run_commands,
        dependencies,
        fingerprint_parts,
    )
    # ── Rust / Go ───────────────────────────────────────────────────────
    _detect_rust(root, package_managers, test_commands, build_commands, run_commands, fingerprint_parts)
    _detect_go(root, package_managers, test_commands, build_commands, run_commands, fingerprint_parts)

    entrypoints = tuple(c for c in _ENTRYPOINT_CANDIDATES if (root / c).is_file())

    # Order languages by prevalence so the primary language is first.
    ordered_langs = tuple(sorted(languages, key=lambda k: (-languages[k], k)))

    fingerprint = hash_content("\n".join(sorted(fingerprint_parts))) if fingerprint_parts else ""

    model = ProjectModel(
        root=str(root),
        languages=ordered_langs,
        package_managers=tuple(dict.fromkeys(package_managers)),  # dedupe, keep order
        frameworks=tuple(dict.fromkeys(frameworks)),
        entrypoints=entrypoints,
        test_commands=tuple(dict.fromkeys(test_commands)),
        build_commands=tuple(dict.fromkeys(build_commands)),
        run_commands=tuple(dict.fromkeys(run_commands)),
        dependencies=tuple(dependencies[:_MAX_DEPS]),
        file_count=file_count,
        indexed_symbols=0,  # symbol indexing arrives with the LSP slice
        fingerprint=fingerprint,
    )
    _log.info(
        "ide.project.analyzed",
        event_type="ide",
        root=str(root),
        languages=list(ordered_langs),
        package_managers=model.package_managers,
        file_count=file_count,
    )
    return model


def _read_text(path: Path) -> str | None:
    try:
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _detect_python(
    root: Path,
    package_managers: list[str],
    frameworks: list[str],
    test_commands: list[str],
    build_commands: list[str],
    dependencies: list[str],
    fingerprint_parts: list[str],
) -> None:
    deps: list[str] = []
    pyproject = _read_text(root / "pyproject.toml")
    if pyproject is not None:
        fingerprint_parts.append(f"pyproject.toml:{hash_content(pyproject)}")
        try:
            data = tomllib.loads(pyproject)
        except tomllib.TOMLDecodeError:
            data = {}
        # PEP 621 deps
        proj = data.get("project", {})
        if isinstance(proj, dict):
            deps += [str(d) for d in proj.get("dependencies", []) if isinstance(d, str)]
        # Manager: uv / poetry / pdm leave their own tables; default to pip.
        if "uv" in data.get("tool", {}) or (root / "uv.lock").is_file():
            package_managers.append("uv")
        elif "poetry" in data.get("tool", {}):
            package_managers.append("poetry")
        else:
            package_managers.append("pip")
    elif (root / "requirements.txt").is_file():
        req = _read_text(root / "requirements.txt") or ""
        fingerprint_parts.append(f"requirements.txt:{hash_content(req)}")
        deps += [ln for ln in (raw.strip() for raw in req.splitlines()) if ln and not ln.startswith("#")]
        package_managers.append("pip")
    else:
        return  # not a python project

    normalised = [_norm_pkg(d) for d in deps]
    dependencies += [d for d in normalised if d]
    for needle, label in _PY_FRAMEWORKS:
        if needle in normalised:
            frameworks.append(label)
    # pytest is the near-universal python test runner; only claim it if present.
    if "pytest" in normalised or (root / "pytest.ini").is_file() or (root / "conftest.py").is_file():
        test_commands.append("pytest")
    build_commands.append("python -m build")


def _detect_node(
    root: Path,
    package_managers: list[str],
    frameworks: list[str],
    test_commands: list[str],
    build_commands: list[str],
    run_commands: list[str],
    dependencies: list[str],
    fingerprint_parts: list[str],
) -> None:
    pkg_raw = _read_text(root / "package.json")
    if pkg_raw is None:
        return
    fingerprint_parts.append(f"package.json:{hash_content(pkg_raw)}")
    try:
        pkg = json.loads(pkg_raw)
    except json.JSONDecodeError:
        return
    if not isinstance(pkg, dict):
        return

    # Manager from the lockfile present in the root.
    if (root / "pnpm-lock.yaml").is_file():
        package_managers.append("pnpm")
    elif (root / "yarn.lock").is_file():
        package_managers.append("yarn")
    elif (root / "bun.lockb").is_file():
        package_managers.append("bun")
    else:
        package_managers.append("npm")
    mgr = package_managers[-1]

    all_deps: dict[str, str] = {}
    for key in ("dependencies", "devDependencies"):
        section = pkg.get(key, {})
        if isinstance(section, dict):
            all_deps.update({str(k): str(v) for k, v in section.items()})
    dependencies += sorted(all_deps)
    for needle, label in _NODE_FRAMEWORKS:
        if needle in all_deps:
            frameworks.append(label)

    # Commands come straight from package.json scripts — authoritative, not guessed.
    scripts = pkg.get("scripts", {})
    if isinstance(scripts, dict):
        run = "run " if mgr == "npm" else ""  # npm needs `run`; pnpm/yarn/bun don't
        if "test" in scripts:
            test_commands.append(f"{mgr} {run}test".replace("  ", " "))
        if "build" in scripts:
            build_commands.append(f"{mgr} {run}build".replace("  ", " "))
        for dev_key in ("dev", "start", "serve"):
            if dev_key in scripts:
                run_commands.append(f"{mgr} {run}{dev_key}".replace("  ", " "))
                break


def _detect_rust(
    root: Path,
    package_managers: list[str],
    test_commands: list[str],
    build_commands: list[str],
    run_commands: list[str],
    fingerprint_parts: list[str],
) -> None:
    cargo = _read_text(root / "Cargo.toml")
    if cargo is None:
        return
    fingerprint_parts.append(f"Cargo.toml:{hash_content(cargo)}")
    package_managers.append("cargo")
    test_commands.append("cargo test")
    build_commands.append("cargo build")
    run_commands.append("cargo run")


def _detect_go(
    root: Path,
    package_managers: list[str],
    test_commands: list[str],
    build_commands: list[str],
    run_commands: list[str],
    fingerprint_parts: list[str],
) -> None:
    gomod = _read_text(root / "go.mod")
    if gomod is None:
        return
    fingerprint_parts.append(f"go.mod:{hash_content(gomod)}")
    package_managers.append("go")
    test_commands.append("go test ./...")
    build_commands.append("go build ./...")
    run_commands.append("go run .")
