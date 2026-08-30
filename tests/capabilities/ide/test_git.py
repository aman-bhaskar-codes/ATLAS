"""Git/SCM status — the PURE parser + the governed `GitEngine.status`.

`parse_git_status` is IO-free: it turns captured `git status --porcelain=v1
--branch` fixtures into a `GitStatus`, so every porcelain shape the review loop
must understand (clean, ahead/behind, untracked, staged, modified, rename,
conflict, detached, no-upstream) is locked against a literal without a subprocess.

`GitEngine.status` composes a `CommandRunner`; here it runs against a fake runner
so we assert two things without git installed: a repo yields the parsed status,
and a non-repo (runner `ok=False`) yields `None` — an honest "no SCM", never a
fabricated clean tree.
"""

from __future__ import annotations

from atlas.capabilities.ide.contracts import CommandResult, GitFileState
from atlas.capabilities.ide.git import GitEngine, parse_git_status, parse_numstat
from atlas.infra.ids import CorrelationId

_CID = CorrelationId("cid-git")


class TestParseBranchHeader:
    def test_tracking_ahead_behind(self) -> None:
        st = parse_git_status("## main...origin/main [ahead 2, behind 3]\n")
        assert st.branch == "main" and st.ahead == 2 and st.behind == 3
        assert st.detached is False and st.changes == ()

    def test_plain_branch_no_upstream(self) -> None:
        st = parse_git_status("## feature/x\n")
        assert st.branch == "feature/x" and st.ahead == 0 and st.behind == 0

    def test_detached_head(self) -> None:
        st = parse_git_status("## HEAD (no branch)\n")
        assert st.branch == "HEAD" and st.detached is True

    def test_no_commits_yet(self) -> None:
        st = parse_git_status("## No commits yet on main\n")
        assert st.branch == "main" and st.detached is False


class TestParseEntries:
    def test_untracked(self) -> None:
        st = parse_git_status("## main\n?? new.txt\n")
        (c,) = st.changes
        assert c.state is GitFileState.UNTRACKED and c.staged is False and c.path == "new.txt"

    def test_staged_addition(self) -> None:
        st = parse_git_status("## main\nA  added.py\n")
        (c,) = st.changes
        assert c.state is GitFileState.STAGED and c.staged is True and c.path == "added.py"

    def test_worktree_modified_unstaged(self) -> None:
        st = parse_git_status("## main\n M edited.py\n")
        (c,) = st.changes
        assert c.state is GitFileState.MODIFIED and c.staged is False

    def test_staged_modified(self) -> None:
        st = parse_git_status("## main\nM  cached.py\n")
        (c,) = st.changes
        assert c.state is GitFileState.MODIFIED and c.staged is True

    def test_deleted(self) -> None:
        st = parse_git_status("## main\n D gone.py\n")
        (c,) = st.changes
        assert c.state is GitFileState.DELETED and c.staged is False

    def test_rename_splits_old_and_new(self) -> None:
        st = parse_git_status("## main\nR  old.py -> new.py\n")
        (c,) = st.changes
        assert c.state is GitFileState.RENAMED and c.staged is True
        assert c.old_path == "old.py" and c.path == "new.py"

    def test_conflict_marked_and_flagged(self) -> None:
        st = parse_git_status("## main\nUU merged.py\n")
        (c,) = st.changes
        assert c.state is GitFileState.CONFLICTED and st.has_conflicts is True

    def test_added_added_conflict(self) -> None:
        st = parse_git_status("## main\nAA both.py\n")
        (c,) = st.changes
        assert c.state is GitFileState.CONFLICTED

    def test_short_line_ignored(self) -> None:
        # A stray too-short line must not crash or invent a change.
        st = parse_git_status("## main\nx\n M real.py\n")
        assert len(st.changes) == 1 and st.changes[0].path == "real.py"

    def test_multiple_changes_and_clean_flag(self) -> None:
        st = parse_git_status("## main\n M a.py\n?? b.py\nA  c.py\n")
        assert len(st.changes) == 3 and st.has_conflicts is False


class _FakeRunner:
    """Stands in for `CommandRunner`: returns a canned `CommandResult`, records
    the command + cwd so we can assert git was invoked through the funnel path."""

    def __init__(self, result: CommandResult) -> None:
        self._result = result
        self.calls: list[tuple[str, str]] = []

    async def run(
        self, command: str, *, cwd: str, correlation_id: CorrelationId, timeout_s: float = 120.0
    ) -> CommandResult:
        self.calls.append((command, cwd))
        return self._result


class TestGitEngineStatus:
    async def test_repo_returns_parsed_status(self) -> None:
        out = "## main...origin/main [ahead 1]\n M edited.py\n"
        runner = _FakeRunner(CommandResult(command="git status", ok=True, stdout=out))
        engine = GitEngine(runner, "/repo")  # type: ignore[arg-type]
        st = await engine.status(correlation_id=_CID)
        assert st is not None and st.branch == "main" and st.ahead == 1
        assert len(st.changes) == 1
        # git ran through the runner in the workspace root.
        assert runner.calls[0][1] == "/repo" and runner.calls[0][0].startswith("git status")

    async def test_non_repo_returns_none(self) -> None:
        runner = _FakeRunner(CommandResult(command="git status", ok=False, error="not a git repository"))
        engine = GitEngine(runner, "/tmp/plain")  # type: ignore[arg-type]
        assert await engine.status(correlation_id=_CID) is None


class TestParseNumstat:
    def test_added_and_removed_counts(self) -> None:
        (s,) = parse_numstat("3\t1\tsrc/a.py\n")
        assert s.path == "src/a.py" and s.added == 3 and s.removed == 1 and s.binary is False

    def test_binary_reports_no_counts(self) -> None:
        (s,) = parse_numstat("-\t-\tlogo.png\n")
        assert s.binary is True and s.added == 0 and s.removed == 0 and s.path == "logo.png"

    def test_plain_rename(self) -> None:
        (s,) = parse_numstat("1\t1\told.py => new.py\n")
        assert s.old_path == "old.py" and s.path == "new.py"

    def test_brace_rename_expands_both_sides(self) -> None:
        (s,) = parse_numstat("0\t0\tsrc/{old => new}/f.py\n")
        assert s.old_path == "src/old/f.py" and s.path == "src/new/f.py"

    def test_blank_and_short_lines_skipped(self) -> None:
        stats = parse_numstat("\n2\t0\treal.py\nbroken\n")
        assert len(stats) == 1 and stats[0].path == "real.py"

    def test_empty_output_is_empty(self) -> None:
        assert parse_numstat("") == ()


class _DiffRunner:
    """Answers `git diff --numstat` and the raw `git diff` separately so the
    two-call `GitEngine.diff` path can be exercised without git installed."""

    def __init__(self, *, ok: bool, numstat: str = "", patch: str = "") -> None:
        self._ok = ok
        self._numstat = numstat
        self._patch = patch
        self.calls: list[str] = []

    async def run(
        self, command: str, *, cwd: str, correlation_id: CorrelationId, timeout_s: float = 120.0
    ) -> CommandResult:
        self.calls.append(command)
        stdout = self._numstat if "--numstat" in command else self._patch
        return CommandResult(command=command, ok=self._ok, stdout=stdout, error=None if self._ok else "not a repo")


class TestGitEngineDiff:
    async def test_diff_parses_stats_and_patch(self) -> None:
        runner = _DiffRunner(ok=True, numstat="3\t1\ta.py\n", patch="diff --git a/a.py b/a.py\n+x\n")
        engine = GitEngine(runner, "/repo")  # type: ignore[arg-type]
        diff = await engine.diff(correlation_id=_CID)
        assert diff is not None and len(diff.files) == 1 and diff.files[0].added == 3
        assert diff.patch.startswith("diff --git") and diff.staged is False
        assert any("--numstat" in c for c in runner.calls)

    async def test_staged_flag_threads_through(self) -> None:
        runner = _DiffRunner(ok=True, numstat="", patch="")
        engine = GitEngine(runner, "/repo")  # type: ignore[arg-type]
        diff = await engine.diff(staged=True, correlation_id=_CID)
        assert diff is not None and diff.staged is True and diff.files == ()
        assert all("--staged" in c for c in runner.calls)

    async def test_non_repo_returns_none(self) -> None:
        runner = _DiffRunner(ok=False)
        engine = GitEngine(runner, "/tmp/plain")  # type: ignore[arg-type]
        assert await engine.diff(correlation_id=_CID) is None
