"""WorkspaceEngine — the read side of the ADE workspace (Phases 2-3).

Pure and in-process: it walks a root on disk into `FileNode`s and reads a file
into a `DocumentSnapshot` + content, stamping every file with a content-hash
`version`. That hash is the currency of the stale-write guard (Phase 13): an
agent plans a `FileChange` against the version it read, and the write side
(a later slice) refuses to apply if the on-disk hash has since moved.

WHY read-only here: listing and reading are Tier-0 (no side effects), so they
need no sandbox and no safety funnel — they run fast, synchronously off the
filesystem. Mutations are consequential and route through `SafetyEngine.guard`
+ the filesystem tool in a separate slice; this engine never writes.

WHY its own tree walk (not the filesystem tool's `_list`): the IDE needs a
recursive, ignore-aware tree with per-file version hashes in ONE shape the
frontend renders directly — the tool's flat single-dir listing is a different
contract aimed at the LLM.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from atlas.capabilities.ide.contracts import (
    DocumentId,
    DocumentSnapshot,
    DocumentStatus,
    FileNode,
    WorkspaceId,
    WorkspaceRef,
)
from atlas.infra.logging import get_logger

_log = get_logger("atlas.ide.workspace")

# Directories never worth walking into for an IDE tree — huge, generated, or VCS
# internals. Kept conservative: real source dirs are never in here.
_IGNORED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".next",
        "dist",
        "build",
        ".atlas",
        ".idea",
        ".vscode",
    }
)

# Read cap: an IDE opening a 400 MB log should not OOM the backend. Mirrors the
# filesystem tool's 100k read cap so the two never disagree on "too big".
_MAX_READ_BYTES = 2_000_000

# Coarse extension→language map so the frontend can pick a Monaco grammar without
# a second round-trip. Unknown extensions fall back to "plaintext".
_LANGUAGE_BY_EXT: dict[str, str] = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".json": "json",
    ".md": "markdown",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".sh": "shell",
    ".html": "html",
    ".css": "css",
    ".sql": "sql",
    ".rs": "rust",
    ".go": "go",
    ".txt": "plaintext",
}


def _language_for(path: Path) -> str:
    return _LANGUAGE_BY_EXT.get(path.suffix.lower(), "plaintext")


def hash_content(content: str) -> str:
    """The stable `version` of a file's content. sha256 of UTF-8 bytes, hex —
    the same value the write side recomputes to detect a stale write."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class WorkspaceError(Exception):
    """A workspace request that cannot be served — bad root, path escape, or an
    unreadable file. Raised (never swallowed into a fake-success) so the caller
    surfaces an honest error rather than an empty tree."""


class WorkspaceEngine:
    """Read side of one workspace root. Holds no mutable session state; every
    method derives fresh from disk so two callers never see stale caches."""

    def __init__(self, ref: WorkspaceRef) -> None:
        if not ref.root_paths:
            raise WorkspaceError("workspace has no root paths")
        # Phase-3 slice: single primary root; multi-root fans out in a later slice.
        self._root = Path(ref.root_paths[0]).expanduser().resolve(strict=False)
        self._ref = ref
        if not self._root.is_dir():
            raise WorkspaceError(f"root path is not a directory: {self._root}")

    @property
    def ref(self) -> WorkspaceRef:
        return self._ref

    @property
    def root(self) -> Path:
        return self._root

    # ---- path safety ----------------------------------------------------
    def _resolve(self, rel: str) -> Path:
        """Resolve a workspace-relative path, refusing any escape past the root
        (`..`, absolute paths, symlink-out). The IDE never serves a file the
        workspace does not contain."""
        candidate = (self._root / rel).resolve(strict=False)
        try:
            candidate.relative_to(self._root)
        except ValueError as exc:
            raise WorkspaceError(f"path escapes workspace root: {rel!r}") from exc
        return candidate

    def _rel(self, path: Path) -> str:
        """Workspace-relative posix path — the wire form the frontend keys on."""
        return path.relative_to(self._root).as_posix()

    # ---- file tree ------------------------------------------------------
    def tree(self, *, max_entries: int = 20_000) -> tuple[FileNode, ...]:
        """The workspace file tree as a flat, sorted tuple of `FileNode`s
        (dirs before files, then name). Flat (not nested) so the frontend can
        virtualize it; ignore-aware so it never drowns in node_modules."""
        nodes: list[FileNode] = []
        self._walk(self._root, nodes, max_entries)
        return tuple(nodes)

    def _walk(self, directory: Path, out: list[FileNode], max_entries: int) -> None:
        try:
            children = sorted(
                directory.iterdir(),
                key=lambda c: (not c.is_dir(), c.name.lower()),
            )
        except OSError as exc:
            _log.warning("ide.tree.unreadable_dir", extra={"dir": str(directory), "error": str(exc)})
            return
        for child in children:
            if len(out) >= max_entries:
                return
            if child.is_dir():
                if child.name in _IGNORED_DIRS:
                    continue
                out.append(FileNode(path=self._rel(child), name=child.name, is_dir=True))
                self._walk(child, out, max_entries)
            elif child.is_file():
                out.append(self._file_node(child))

    def _file_node(self, path: Path) -> FileNode:
        try:
            size = path.stat().st_size
        except OSError:
            size = None
        # Only hash files small enough to read cheaply; huge/binary files carry
        # no version (the write guard treats a missing version as "unknown").
        version: str | None = None
        if size is not None and size <= _MAX_READ_BYTES:
            try:
                version = hash_content(path.read_text(errors="replace"))
            except OSError:
                version = None
        return FileNode(
            path=self._rel(path),
            name=path.name,
            is_dir=False,
            size=size,
            version=version,
            language=_language_for(path),
        )

    # ---- document read --------------------------------------------------
    def read_document(self, rel_path: str) -> tuple[DocumentSnapshot, str]:
        """Open a file: its server-truth `DocumentSnapshot` (id, language,
        version, line count) plus the content. `version` is the hash an agent
        must echo back as `expected_version` to write safely."""
        path = self._resolve(rel_path)
        if not path.is_file():
            raise WorkspaceError(f"not a file: {rel_path!r}")
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise WorkspaceError(f"read failed: {rel_path!r}: {exc}") from exc
        if len(raw) > _MAX_READ_BYTES:
            raise WorkspaceError(f"file too large to open ({len(raw)} bytes): {rel_path!r}")
        content = raw.decode("utf-8", errors="replace")
        snapshot = DocumentSnapshot(
            id=DocumentId(self._rel(path)),  # path IS the stable id within a workspace
            path=self._rel(path),
            language=_language_for(path),
            version=hash_content(content),
            status=DocumentStatus.CLEAN,
            line_count=content.count("\n") + (1 if content and not content.endswith("\n") else 0),
        )
        return snapshot, content


def open_workspace(workspace_id: str, name: str, root_paths: tuple[str, ...], *, now_ts: str) -> WorkspaceEngine:
    """Construct a `WorkspaceEngine` for a fresh `WorkspaceRef`. The composition
    root/session store owns id allocation and timestamps; this just assembles."""
    ref = WorkspaceRef(
        id=WorkspaceId(workspace_id),
        name=name,
        root_paths=root_paths,
        created_ts=now_ts,
        last_opened_ts=now_ts,
    )
    return WorkspaceEngine(ref)
