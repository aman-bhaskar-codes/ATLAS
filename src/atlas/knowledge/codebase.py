"""CodebaseKnowledge — index a repository into the fabric (§48-49).

Git-aware: files come from `git ls-files` when the repo is a git checkout
(so .gitignore is respected for free), the current HEAD commit is recorded
for diagnostics, and re-ingestion after changes is cheap because content-hash
dedupe skips untouched files (§24).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from atlas.infra.logging import get_logger
from atlas.knowledge.domain import IngestionJob, SourceType
from atlas.knowledge.ingestion import IngestionPipeline

_log = get_logger("atlas.knowledge.codebase")

_INDEXED_SUFFIXES = frozenset(
    {
        ".py", ".md", ".rst", ".txt", ".yaml", ".yml", ".toml", ".json", ".ini", ".cfg",
        ".ts", ".tsx", ".js", ".jsx", ".sh", ".sql", ".html", ".css", ".csv",
    }
)
_SKIP_DIRS = frozenset(
    {".git", ".venv", "node_modules", "__pycache__", ".next", ".mypy_cache", ".pytest_cache", ".ruff_cache"}
)
_MAX_FILE_BYTES = 200_000


class CodebaseKnowledge:
    def __init__(self, pipeline: IngestionPipeline, *, max_files: int = 200) -> None:
        self._pipeline = pipeline
        self._max_files = max_files

    async def ingest_repo(self, root: Path) -> list[IngestionJob]:
        head = _git_head(root)
        paths = _list_files(root)[: self._max_files]
        jobs: list[IngestionJob] = []
        for path in paths:
            try:
                if path.stat().st_size > _MAX_FILE_BYTES:
                    continue
                job = await self._pipeline.ingest_file(path, source_type=SourceType.LOCAL_FILE)
                jobs.append(job)
                _log.debug("codebase.ingested", event_type="knowledge", path=str(path), state=job.state.value)
            except Exception as exc:
                _log.warning("codebase.ingest_failed", event_type="knowledge", path=str(path), error=repr(exc))
        _log.info(
            "codebase.indexed", event_type="knowledge", root=str(root), files=len(jobs), head=head or "no-git"
        )
        return jobs


def _git_head(root: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, timeout=5, check=False
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _list_files(root: Path) -> list[Path]:
    """git ls-files when available (respects .gitignore); rglob otherwise."""
    try:
        out = subprocess.run(
            ["git", "ls-files"], cwd=root, capture_output=True, text=True, timeout=15, check=False
        )
        if out.returncode == 0 and out.stdout.strip():
            paths = [root / line for line in out.stdout.splitlines() if line.strip()]
            return [p for p in paths if p.suffix.lower() in _INDEXED_SUFFIXES and p.is_file()]
    except (OSError, subprocess.TimeoutExpired):
        pass
    found: list[Path] = []
    for path in root.rglob("*"):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if path.is_file() and path.suffix.lower() in _INDEXED_SUFFIXES:
            found.append(path)
    return found
