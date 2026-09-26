"""Git write operations — stage / unstage / commit / branch (Slice 9).

The read side (status / diff) lives in `git.py`; this is its write counterpart.
Like every IDE mutation, `GitOps` owns NO execution: it delegates to
`CommandRunner.run` (the single `SafetyEngine.guard` funnel), so a commit is
governed exactly as any other shell dispatch — git is never a side door around
ATLAS policy (Constitution). The engine never shells out itself.

SECURITY: every user-supplied value (paths, commit message, branch name) is
shell-quoted with `shlex.quote` before it enters the command string, so a path
like ``foo; rm -rf /`` becomes one inert argument. Nothing is interpolated raw.

HONESTY: `ok` / `error` reflect the REAL git process; `denied` means the funnel
refused and nothing ran; committing with nothing staged is git's own non-zero
exit surfaced as `ok=False` with git's message — never a fabricated success.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Sequence

from atlas.capabilities.ide.commands import CommandRunner
from atlas.capabilities.ide.contracts import CommandResult, GitOpResult
from atlas.infra.ids import CorrelationId

_DETAIL_LIMIT = 500

# `git commit` prints `[branch sha] summary`, or `[branch (root-commit) sha]
# summary` for the first commit. The sha is the short hash.
_COMMIT_LINE = re.compile(
    r"^\[(?P<branch>[^\s\]]+)\s+(?:\(root-commit\)\s+)?(?P<sha>[0-9a-f]+)\]",
    re.MULTILINE,
)


def _clip(text: str) -> str:
    text = text.strip()
    return text if len(text) <= _DETAIL_LIMIT else text[:_DETAIL_LIMIT] + "…"


class GitOps:
    """Governed git writes over one workspace root. Holds a `CommandRunner` (the
    same funnel every IDE command uses) + the workspace root. Stateless beyond
    that — one instance per resolved workspace."""

    def __init__(self, runner: CommandRunner, root: str) -> None:
        self._runner = runner
        self._root = root

    async def _run(self, command: str, *, correlation_id: CorrelationId) -> CommandResult:
        return await self._runner.run(command, cwd=self._root, correlation_id=correlation_id)

    async def stage(
        self,
        paths: Sequence[str],
        *,
        all_changes: bool = False,
        correlation_id: CorrelationId,
    ) -> GitOpResult:
        """Stage `paths` (or everything with `all_changes=True`) via `git add`. Each
        path is shell-quoted; nothing is interpolated raw."""
        if all_changes:
            command = "git add -A"
            detail = "staged all changes"
        elif paths:
            quoted = " ".join(shlex.quote(p) for p in paths)
            command = f"git add -- {quoted}"
            detail = f"staged {len(paths)} path{'' if len(paths) == 1 else 's'}"
        else:
            return GitOpResult(action="stage", error="no paths given to stage")
        return _outcome("stage", await self._run(command, correlation_id=correlation_id), detail=detail)

    async def unstage(
        self,
        paths: Sequence[str],
        *,
        all_changes: bool = False,
        correlation_id: CorrelationId,
    ) -> GitOpResult:
        """Unstage `paths` (or everything with `all_changes=True`) via
        `git restore --staged`. Worktree contents are untouched — only the index."""
        if all_changes:
            command = "git restore --staged -- ."
            detail = "unstaged all changes"
        elif paths:
            quoted = " ".join(shlex.quote(p) for p in paths)
            command = f"git restore --staged -- {quoted}"
            detail = f"unstaged {len(paths)} path{'' if len(paths) == 1 else 's'}"
        else:
            return GitOpResult(action="unstage", error="no paths given to unstage")
        return _outcome("unstage", await self._run(command, correlation_id=correlation_id), detail=detail)

    async def commit(self, message: str, *, correlation_id: CorrelationId) -> GitOpResult:
        """Commit the staged index with `message` (shell-quoted). A commit with
        nothing staged is git's own non-zero exit, surfaced honestly as `ok=False`.
        On success the new short SHA is parsed from git's `[branch sha]` line."""
        message = message.strip()
        if not message:
            return GitOpResult(action="commit", error="empty commit message")
        command = f"git commit -m {shlex.quote(message)}"
        result = await self._run(command, correlation_id=correlation_id)
        outcome = _outcome("commit", result, detail=_first_line(result.stdout))
        if not outcome.ok:
            return outcome
        match = _COMMIT_LINE.search(result.stdout)
        return outcome.model_copy(
            update={
                "commit": match.group("sha") if match else None,
                "branch": match.group("branch") if match else None,
            }
        )

    async def create_branch(self, name: str, *, correlation_id: CorrelationId) -> GitOpResult:
        """Create AND switch to a new branch via `git checkout -b` (name quoted)."""
        name = name.strip()
        if not name:
            return GitOpResult(action="branch", error="empty branch name")
        command = f"git checkout -b {shlex.quote(name)}"
        result = await self._run(command, correlation_id=correlation_id)
        outcome = _outcome("branch", result, detail=f"created branch {name}")
        return outcome.model_copy(update={"branch": name}) if outcome.ok else outcome

    async def checkout_branch(self, name: str, *, correlation_id: CorrelationId) -> GitOpResult:
        """Switch to an existing branch via `git checkout` (name quoted)."""
        name = name.strip()
        if not name:
            return GitOpResult(action="checkout", error="empty branch name")
        command = f"git checkout {shlex.quote(name)}"
        result = await self._run(command, correlation_id=correlation_id)
        outcome = _outcome("checkout", result, detail=f"switched to {name}")
        return outcome.model_copy(update={"branch": name}) if outcome.ok else outcome


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _outcome(action: str, result: CommandResult, *, detail: str = "") -> GitOpResult:
    """Fold a governed `CommandResult` into a `GitOpResult`, preserving the three
    honest outcomes: denied (nothing ran), git failure (real message), success."""
    if result.denied:
        return GitOpResult(action=action, denied=True, error=result.error)
    if not result.ok:
        msg = result.stderr or result.stdout or result.error or "git command failed"
        return GitOpResult(action=action, ok=False, error=_clip(msg))
    return GitOpResult(action=action, ok=True, detail=_clip(detail))
