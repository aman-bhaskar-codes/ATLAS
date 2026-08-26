"""filesystem_tool — permitted-path file operations.

WHY dry_run enumerates targets: the mass_deletion hard-block matcher needs a
REAL count. A `delete` of a directory reports how many files it would remove;
the classifier reads that (via args) and blocks if it exceeds the threshold.
Reads/searches are Tier-0/1 and run in-process (fast, no container). Writes and
deletes are consequential and go through the sandbox so even a path bug can't
escape the mounted dir.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from atlas.infra.logging import get_logger
from atlas.infra.types import SideEffect, ToolResult
from atlas.safety.sandbox import Sandbox
from atlas.tools.paths import PathError, resolve_in_allowlist

_log = get_logger("atlas.tools.fs")


class FilesystemTool:
    name = "filesystem"

    def __init__(
        self,
        *,
        read_globs: list[str],
        write_globs: list[str],
        sandbox: Sandbox,
    ) -> None:
        self._read_globs = read_globs
        self._write_globs = write_globs
        self._sandbox = sandbox

    # ---- dry_run: the security-relevant preview -------------------------
    def dry_run(self, args: dict[str, Any]) -> str:
        op = str(args.get("operation", ""))
        path = str(args.get("path", ""))
        if op == "delete":
            count = self._count_delete_targets(path)
            return f"DELETE {count} item(s) under {path!r} (irreversible)"
        if op == "write":
            return f"WRITE {len(str(args.get('content', '')))} bytes to {path!r}"
        if op == "read":
            return f"READ {path!r}"
        if op == "search":
            return f"SEARCH {args.get('query')!r} under {path!r}"
        return f"unknown filesystem op {op!r}"

    def _count_delete_targets(self, path: str) -> int:
        try:
            p = Path(path).expanduser()
            if p.is_file():
                return 1
            if p.is_dir():
                return sum(1 for _ in p.rglob("*"))
            return 0
        except OSError:
            return 0

    # ---- execute --------------------------------------------------------
    async def execute(self, args: dict[str, Any]) -> ToolResult:
        op = str(args.get("operation", ""))
        try:
            if op == "read":
                return await self._read(str(args["path"]))
            if op in ("search",):
                return await self._search(str(args["path"]), str(args.get("query", "")))
            if op == "write":
                return await self._write(str(args["path"]), str(args.get("content", "")))
            if op == "delete":
                return await self._delete(str(args["path"]))
            # Tier-0 read-only ops the LLM commonly generates
            if op in ("list", "list_directory", "inspect", "tree", "stat"):
                return await self._list(str(args.get("path", ".")))
            # Catch any read_X variant (e.g. read_file, read_content)
            if op.startswith("read_"):
                return await self._read(str(args.get("path", ".")))
            return ToolResult(ok=False, error=f"unknown operation {op!r}")
        except PathError as exc:
            return ToolResult(ok=False, error=str(exc))
        except KeyError as exc:
            return ToolResult(ok=False, error=f"missing argument {exc}")

    async def _read(self, path: str) -> ToolResult:
        rp = resolve_in_allowlist(path, self._read_globs)
        try:
            text = rp.host.read_text()
        except OSError as exc:
            return ToolResult(ok=False, error=f"read failed: {exc}")
        return ToolResult(ok=True, output={"path": str(rp.host), "content": text[:100_000]})

    async def _search(self, path: str, query: str) -> ToolResult:
        p = Path(path).expanduser().resolve(strict=False)
        # Pure-Python fallback so search works even without Docker/ripgrep
        try:
            matches: list[str] = []
            target = p if p.is_dir() else p.parent
            for file in target.rglob("*"):
                if not file.is_file():
                    continue
                try:
                    text = file.read_text(errors="replace")
                    for lineno, line in enumerate(text.splitlines(), 1):
                        if query.lower() in line.lower():
                            matches.append(f"{file}:{lineno}: {line.rstrip()}")
                            if len(matches) >= 200:
                                break
                except OSError:
                    pass
                if len(matches) >= 200:
                    break
            return ToolResult(ok=True, output={"matches": "\n".join(matches) or "(no matches)"})
        except OSError as exc:
            return ToolResult(ok=False, error=f"search failed: {exc}")

    async def _list(self, path: str) -> ToolResult:
        """Directory listing — the most common LLM-generated filesystem op."""
        p = Path(path).expanduser().resolve(strict=False)
        try:
            if p.is_file():
                # Model asked to "list" a file — just read it
                return await self._read(path)
            entries = []
            for child in sorted(p.iterdir()):
                kind = "dir" if child.is_dir() else "file"
                size = child.stat().st_size if child.is_file() else ""
                entries.append({"name": child.name, "type": kind, "size": size})
            return ToolResult(ok=True, output={"path": str(p), "entries": entries, "count": len(entries)})
        except OSError as exc:
            return ToolResult(ok=False, error=f"list failed: {exc}")

    async def _write(self, path: str, content: str) -> ToolResult:
        rp = resolve_in_allowlist(path, self._write_globs)
        # Safe write: use 'tee' with an explicit path argument — no shell.
        result = await self._sandbox.run(
            ["tee", rp.container],
            mounts={str(rp.mount_source): rp.mount_target},
            network=False,
            timeout_s=15.0,
            stdin=content.encode("utf-8"),
        )
        if result.exit_code != 0:
            return ToolResult(ok=False, error=result.stderr_tail or "write failed")
        return ToolResult(
            ok=True,
            output={"path": str(rp.host), "bytes": len(content)},
            side_effects=(
                SideEffect(kind="file_write", target=str(rp.host), detail=f"{len(content)} bytes", reversible=False),
            ),
        )

    async def _delete(self, path: str) -> ToolResult:
        rp = resolve_in_allowlist(path, self._write_globs)
        result = await self._sandbox.run(
            ["rm", "-rf", rp.container],
            mounts={str(rp.mount_source): rp.mount_target},
            network=False,
            timeout_s=15.0,
        )
        if result.exit_code != 0:
            return ToolResult(ok=False, error=result.stderr_tail or "delete failed")
        return ToolResult(
            ok=True,
            output={"path": str(rp.host)},
            side_effects=(SideEffect(kind="file_delete", target=str(rp.host), reversible=False),),
        )
