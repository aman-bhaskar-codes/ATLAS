"""Git / SCM status — the read side of source control (Phase 8).

Two pieces, split like `editing.py`:

  * `parse_git_status` — PURE. Turns the text of `git status --porcelain=v1
    --branch` into a `GitStatus`. No IO, no funnel: the one place porcelain
    parsing lives, trivially unit-testable against captured fixtures.

  * `GitEngine.status` — the governed read. It runs the git command through the
    SAME `CommandRunner` (→ `SafetyEngine.guard` → command tool) every other IDE
    command uses, then parses the output. The engine never shells out itself, so
    git is not a side door around ATLAS policy (Constitution). A directory that
    is not a git repo (git exits non-zero) yields `None`, an honest "no SCM here"
    rather than a fabricated clean status.

Porcelain v1 (not v2) is deliberate: its `XY PATH` shape is stable and simple to
parse. `X` is the index (staged) side, `Y` the worktree side; `??` is untracked
and any `U`/`AA`/`DD` code is an unmerged conflict. Commit/branch/diff verbs are
later slices — this locks the status read the agentic review loop opens with.
"""

from __future__ import annotations

from atlas.capabilities.ide.commands import CommandRunner
from atlas.capabilities.ide.contracts import DiffStat, GitDiff, GitFileChange, GitFileState, GitStatus
from atlas.infra.ids import CorrelationId
from atlas.infra.logging import get_logger

_log = get_logger("atlas.ide.git")

_STATUS_COMMAND = "git status --porcelain=v1 --branch"

# Porcelain status letter → domain state. `A`/`C` (added/copied to index) have no
# dedicated enum member; they are staged additions, so map to STAGED.
_LETTER_STATE: dict[str, GitFileState] = {
    "M": GitFileState.MODIFIED,
    "T": GitFileState.MODIFIED,  # type change — treat as a modification
    "A": GitFileState.STAGED,
    "C": GitFileState.STAGED,
    "D": GitFileState.DELETED,
    "R": GitFileState.RENAMED,
}


def parse_git_status(output: str) -> GitStatus:
    """Parse `git status --porcelain=v1 --branch` text into a `GitStatus`."""
    branch, ahead, behind, detached = "", 0, 0, False
    changes: list[GitFileChange] = []
    for line in output.splitlines():
        if line.startswith("## "):
            branch, ahead, behind, detached = _parse_branch(line[3:])
        elif line.strip():
            change = _parse_entry(line)
            if change is not None:
                changes.append(change)
    return GitStatus(
        branch=branch,
        ahead=ahead,
        behind=behind,
        detached=detached,
        changes=tuple(changes),
        has_conflicts=any(c.state is GitFileState.CONFLICTED for c in changes),
    )


def _parse_branch(body: str) -> tuple[str, int, int, bool]:
    """Parse the `## ...` header body (leading `## ` already stripped)."""
    if body.startswith("HEAD (no branch)"):
        return "HEAD", 0, 0, True  # detached HEAD
    if body.startswith("No commits yet on "):
        return body[len("No commits yet on ") :].strip(), 0, 0, False

    ahead = behind = 0
    if " [" in body and body.endswith("]"):
        head, bracket = body.split(" [", 1)
        for part in bracket[:-1].split(", "):
            if part.startswith("ahead "):
                ahead = int(part[len("ahead ") :])
            elif part.startswith("behind "):
                behind = int(part[len("behind ") :])
    else:
        head = body
    branch = head.split("...", 1)[0].strip()  # drop the ...upstream suffix
    return branch, ahead, behind, False


def _parse_entry(line: str) -> GitFileChange | None:
    """Parse one `XY PATH` porcelain entry into a `GitFileChange`."""
    if len(line) < 4:
        return None
    xy, rest = line[:2], line[3:]
    x, y = xy[0], xy[1]

    if xy == "??":
        return GitFileChange(path=rest, state=GitFileState.UNTRACKED, staged=False)
    if "U" in xy or xy in {"AA", "DD"}:
        return GitFileChange(path=rest, state=GitFileState.CONFLICTED, staged=False)

    old_path: str | None = None
    path = rest
    if ("R" in xy or "C" in xy) and " -> " in rest:
        old_path, path = rest.split(" -> ", 1)

    staged = x not in (" ", "?")
    letter = x if staged else y
    state = _LETTER_STATE.get(letter, GitFileState.MODIFIED)
    return GitFileChange(path=path, state=state, staged=staged, old_path=old_path)


def parse_numstat(output: str) -> tuple[DiffStat, ...]:
    """Parse `git diff --numstat` text into per-file `DiffStat`s.

    Each line is `<added>\\t<removed>\\t<path>`; a binary file prints `-`/`-` for
    the counts. A rename shows either `old => new` or the compact
    `pre{old => new}post` form, which we expand back to the full old/new paths."""
    stats: list[DiffStat] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        a, r = parts[0], parts[1]
        path = "\t".join(parts[2:])
        binary = a == "-" or r == "-"
        old_path: str | None = None
        if " => " in path:
            old_path, path = _split_rename(path)
        stats.append(
            DiffStat(
                path=path,
                added=0 if binary else int(a),
                removed=0 if binary else int(r),
                binary=binary,
                old_path=old_path,
            )
        )
    return tuple(stats)


def _split_rename(path: str) -> tuple[str, str]:
    """Expand a numstat rename token into (old_path, new_path). Handles the
    compact brace form `src/{old => new}/f.py` and the plain `old => new`."""
    if "{" in path and "}" in path:
        prefix, rest = path.split("{", 1)
        mid, suffix = rest.split("}", 1)
        old_part, new_part = mid.split(" => ", 1)
        return f"{prefix}{old_part}{suffix}", f"{prefix}{new_part}{suffix}"
    old, new = path.split(" => ", 1)
    return old, new


class GitEngine:
    """Governed git reads over one workspace. Holds a `CommandRunner` (the same
    funnel every IDE command uses) + the workspace root. Stateless beyond that."""

    def __init__(self, runner: CommandRunner, root: str) -> None:
        self._runner = runner
        self._root = root

    async def status(self, *, correlation_id: CorrelationId) -> GitStatus | None:
        """Return the working-tree status, or `None` if the root is not a git repo
        (or git is unavailable / refused). Never raises for those expected cases."""
        result = await self._runner.run(_STATUS_COMMAND, cwd=self._root, correlation_id=correlation_id)
        if not result.ok:
            _log.info("ide.git.not_a_repo", event_type="ide", root=self._root, error=result.error)
            return None
        return parse_git_status(result.stdout)

    async def diff(self, *, staged: bool = False, correlation_id: CorrelationId) -> GitDiff | None:
        """Return the worktree diff (or the staged diff when `staged=True`), or
        `None` when the root is not a git repo / git is refused. Read-only: runs
        `git diff [--staged] --numstat` for structured per-file stats and a second
        `git diff [--staged]` for the raw patch — both through the SAME governed
        funnel, so git is never a side door. An empty diff is an honest empty
        `GitDiff`, not `None` (that distinguishes 'clean' from 'no repo')."""
        flag = " --staged" if staged else ""
        numstat = await self._runner.run(f"git diff{flag} --numstat", cwd=self._root, correlation_id=correlation_id)
        if not numstat.ok:
            _log.info("ide.git.diff_unavailable", event_type="ide", root=self._root, error=numstat.error)
            return None
        patch = await self._runner.run(f"git diff{flag}", cwd=self._root, correlation_id=correlation_id)
        return GitDiff(
            staged=staged,
            files=parse_numstat(numstat.stdout),
            patch=patch.stdout if patch.ok else "",
        )
