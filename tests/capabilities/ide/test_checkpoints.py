"""Workspace checkpoints — the governed `GitCheckpoints` (snapshot / restore / list).

`GitCheckpoints` owns NO execution: it composes a `CommandRunner` and builds git
command strings, shell-quoting every value. Here it runs against a scripted fake
runner so we assert — without git installed — that:
  * a snapshot with a dirty tree pins the `git stash create` sha under the hidden
    checkpoint ref, side-effect free (no add/commit),
  * a clean tree (empty stash-create output) falls back to pinning HEAD (clean=True),
  * restore resolves the ref then replays it with `git checkout <sha> -- .`,
  * an unknown checkpoint id is an honest ok=False (never a fabricated success),
  * a policy refusal folds to denied=True,
  * the ref/label/sha are all shell-quoted (injection payloads become inert).
"""

from __future__ import annotations

import shlex

from atlas.capabilities.ide.checkpoints import GitCheckpoints
from atlas.capabilities.ide.contracts import CommandResult
from atlas.infra.ids import CorrelationId

_CID = CorrelationId("cid-cp")


class _ScriptedRunner:
    """Returns canned results per call in order, recording (command, cwd) so we can
    assert the exact governed command sequence. A single result is reused for every
    call when only one is supplied."""

    def __init__(self, results: list[CommandResult]) -> None:
        self._results = results
        self.calls: list[tuple[str, str]] = []

    async def run(
        self, command: str, *, cwd: str, correlation_id: CorrelationId, timeout_s: float = 120.0
    ) -> CommandResult:
        self.calls.append((command, cwd))
        idx = min(len(self.calls) - 1, len(self._results) - 1)
        return self._results[idx]


def _cps(results: list[CommandResult]) -> tuple[GitCheckpoints, _ScriptedRunner]:
    runner = _ScriptedRunner(results)
    return GitCheckpoints(runner, "/repo"), runner  # type: ignore[arg-type]


class TestSnapshot:
    async def test_dirty_tree_pins_stash_create_sha(self) -> None:
        cps, runner = _cps(
            [
                CommandResult(command="git stash create", ok=True, stdout="deadbeefcafe\n"),
                CommandResult(command="git update-ref", ok=True),
            ]
        )
        res = await cps.snapshot("cp1", label="before risky edit", correlation_id=_CID)
        assert res.ok is True and res.action == "snapshot"
        assert res.checkpoint_id == "cp1" and res.commit == "deadbeefca" and res.clean is False
        # stash create came first, side-effect free (no `git add`/`git commit`)
        assert runner.calls[0][0] == "git stash create"
        assert runner.calls[0][1] == "/repo"
        # then the sha is pinned under the hidden ref, message + ref + sha all quoted
        update = runner.calls[1][0]
        assert update.startswith("git update-ref -m ")
        assert shlex.quote("refs/atlas/checkpoints/cp1") in update
        assert shlex.quote("deadbeefcafe") in update
        assert not any("git add" in c or "git commit" in c for c, _ in runner.calls)

    async def test_clean_tree_pins_head(self) -> None:
        cps, runner = _cps(
            [
                CommandResult(command="git stash create", ok=True, stdout="\n"),  # clean → empty
                CommandResult(command="git rev-parse HEAD", ok=True, stdout="1234567890ab\n"),
                CommandResult(command="git update-ref", ok=True),
            ]
        )
        res = await cps.snapshot("cp2", correlation_id=_CID)
        assert res.ok is True and res.clean is True and res.commit == "1234567890"
        assert runner.calls[1][0] == "git rev-parse HEAD"

    async def test_stash_create_failure_is_honest(self) -> None:
        cps, _ = _cps(
            [CommandResult(command="git stash create", ok=False, exit_code=128, stderr="fatal: not a git repository")]
        )
        res = await cps.snapshot("cp3", correlation_id=_CID)
        assert res.ok is False and res.error == "fatal: not a git repository"

    async def test_label_is_shell_quoted(self) -> None:
        cps, runner = _cps(
            [
                CommandResult(command="git stash create", ok=True, stdout="abc123\n"),
                CommandResult(command="git update-ref", ok=True),
            ]
        )
        await cps.snapshot("cp4", label="fix: `rm -rf /` & stuff", correlation_id=_CID)
        assert shlex.quote("fix: `rm -rf /` & stuff") in runner.calls[1][0]


class TestRestore:
    async def test_restore_resolves_then_checks_out(self) -> None:
        cps, runner = _cps(
            [
                CommandResult(command="git rev-parse", ok=True, stdout="deadbeefcafe\n"),
                CommandResult(command="git checkout", ok=True),
            ]
        )
        res = await cps.restore("cp1", correlation_id=_CID)
        assert res.ok is True and res.action == "restore" and res.commit == "deadbeefca"
        assert runner.calls[0][0] == f"git rev-parse --verify --quiet {shlex.quote('refs/atlas/checkpoints/cp1')}"
        assert runner.calls[1][0] == f"git checkout {shlex.quote('deadbeefcafe')} -- ."

    async def test_unknown_checkpoint_is_honest_failure(self) -> None:
        # rev-parse --verify --quiet exits non-zero with empty stdout for a missing ref
        cps, runner = _cps([CommandResult(command="git rev-parse", ok=False, exit_code=1, stdout="")])
        res = await cps.restore("missing", correlation_id=_CID)
        assert res.ok is False and res.error and "unknown checkpoint missing" in res.error
        assert len(runner.calls) == 1  # never attempted a checkout

    async def test_checkout_failure_is_honest(self) -> None:
        cps, _ = _cps(
            [
                CommandResult(command="git rev-parse", ok=True, stdout="abc123\n"),
                CommandResult(command="git checkout", ok=False, exit_code=1, stderr="error: pathspec"),
            ]
        )
        res = await cps.restore("cp1", correlation_id=_CID)
        assert res.ok is False and res.error == "error: pathspec"


class TestList:
    async def test_parses_for_each_ref_lines(self) -> None:
        out = (
            "refs/atlas/checkpoints/cp1\tdeadbee\tbefore edit\n"
            "refs/atlas/checkpoints/cp2\t1234567\tatlas checkpoint cp2\n"
        )
        cps, runner = _cps([CommandResult(command="git for-each-ref", ok=True, stdout=out)])
        refs = await cps.list(correlation_id=_CID)
        assert len(refs) == 2
        assert refs[0].checkpoint_id == "cp1" and refs[0].commit == "deadbee" and refs[0].label == "before edit"
        assert refs[1].checkpoint_id == "cp2"
        assert "for-each-ref" in runner.calls[0][0]

    async def test_non_git_root_is_empty(self) -> None:
        cps, _ = _cps([CommandResult(command="git for-each-ref", ok=False, exit_code=128)])
        assert await cps.list(correlation_id=_CID) == ()


class TestDenied:
    async def test_snapshot_denied_folds(self) -> None:
        cps, _ = _cps([CommandResult(command="git stash create", denied=True, error="denied: policy")])
        res = await cps.snapshot("cp1", correlation_id=_CID)
        assert res.denied is True and res.ok is False and res.error == "denied: policy"

    async def test_restore_denied_folds(self) -> None:
        cps, _ = _cps([CommandResult(command="git rev-parse", denied=True, error="denied: policy")])
        res = await cps.restore("cp1", correlation_id=_CID)
        assert res.denied is True and res.ok is False and res.error == "denied: policy"
