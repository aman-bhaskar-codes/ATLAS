"""Git WRITE operations — the governed `GitOps` (stage / unstage / commit / branch).

`GitOps` owns NO execution: it composes a `CommandRunner` and builds git command
strings, shell-quoting every user-supplied value. Here it runs against a fake
runner so we assert — without git installed — that:
  * each verb reaches the funnel with the RIGHT quoted command in the workspace root,
  * a policy refusal folds to `denied=True` (nothing fabricated),
  * a git non-zero exit folds to an honest `ok=False` with git's own message,
  * a successful commit parses the new short SHA + branch from `[branch sha]`,
  * injection payloads (`foo; rm -rf /`) become ONE inert shell-quoted argument.
"""

from __future__ import annotations

import shlex

from atlas.capabilities.ide.contracts import CommandResult
from atlas.capabilities.ide.git_ops import GitOps
from atlas.infra.ids import CorrelationId

_CID = CorrelationId("cid-gitops")


class _FakeRunner:
    """Stands in for `CommandRunner`: returns a canned `CommandResult`, records the
    command + cwd so we can assert git ran through the funnel with the right args."""

    def __init__(self, result: CommandResult) -> None:
        self._result = result
        self.calls: list[tuple[str, str]] = []

    async def run(
        self, command: str, *, cwd: str, correlation_id: CorrelationId, timeout_s: float = 120.0
    ) -> CommandResult:
        self.calls.append((command, cwd))
        return self._result


def _ops(result: CommandResult) -> tuple[GitOps, _FakeRunner]:
    runner = _FakeRunner(result)
    return GitOps(runner, "/repo"), runner  # type: ignore[arg-type]


class TestStage:
    async def test_stage_all_changes(self) -> None:
        ops, runner = _ops(CommandResult(command="git add -A", ok=True))
        res = await ops.stage([], all_changes=True, correlation_id=_CID)
        assert res.ok is True and res.action == "stage"
        assert runner.calls[0] == ("git add -A", "/repo")

    async def test_stage_paths_are_shell_quoted(self) -> None:
        ops, runner = _ops(CommandResult(command="git add", ok=True))
        res = await ops.stage(["a.py", "b c.py"], correlation_id=_CID)
        assert res.ok is True
        cmd = runner.calls[0][0]
        assert cmd == "git add -- a.py 'b c.py'"

    async def test_stage_no_paths_is_error_no_run(self) -> None:
        ops, runner = _ops(CommandResult(command="", ok=True))
        res = await ops.stage([], correlation_id=_CID)
        assert res.ok is False and res.error and "no paths" in res.error
        assert runner.calls == []  # nothing ran

    async def test_injection_payload_is_inert_single_arg(self) -> None:
        ops, runner = _ops(CommandResult(command="git add", ok=True))
        await ops.stage(["foo; rm -rf /"], correlation_id=_CID)
        cmd = runner.calls[0][0]
        # the whole payload is ONE quoted token — no bare `;` splits the command.
        assert shlex.quote("foo; rm -rf /") in cmd
        assert cmd == f"git add -- {shlex.quote('foo; rm -rf /')}"


class TestUnstage:
    async def test_unstage_all(self) -> None:
        ops, runner = _ops(CommandResult(command="git restore", ok=True))
        res = await ops.unstage([], all_changes=True, correlation_id=_CID)
        assert res.ok is True and res.action == "unstage"
        assert runner.calls[0][0] == "git restore --staged -- ."

    async def test_unstage_paths_quoted(self) -> None:
        ops, runner = _ops(CommandResult(command="git restore", ok=True))
        await ops.unstage(["x y.py"], correlation_id=_CID)
        assert runner.calls[0][0] == "git restore --staged -- 'x y.py'"


class TestCommit:
    async def test_commit_parses_sha_and_branch(self) -> None:
        out = "[main 1a2b3c4] add feature\n 1 file changed, 2 insertions(+)\n"
        ops, runner = _ops(CommandResult(command="git commit", ok=True, stdout=out))
        res = await ops.commit("add feature", correlation_id=_CID)
        assert res.ok is True and res.commit == "1a2b3c4" and res.branch == "main"
        assert runner.calls[0][0] == f"git commit -m {shlex.quote('add feature')}"

    async def test_commit_root_commit_parses_sha(self) -> None:
        out = "[main (root-commit) abc1234] first\n"
        ops, _ = _ops(CommandResult(command="git commit", ok=True, stdout=out))
        res = await ops.commit("first", correlation_id=_CID)
        assert res.commit == "abc1234" and res.branch == "main"

    async def test_nothing_to_commit_is_honest_failure(self) -> None:
        ops, _ = _ops(
            CommandResult(
                command="git commit",
                ok=False,
                exit_code=1,
                stdout="nothing to commit, working tree clean",
            )
        )
        res = await ops.commit("noop", correlation_id=_CID)
        assert res.ok is False and res.commit is None
        assert res.error and "nothing to commit" in res.error

    async def test_empty_message_is_error_no_run(self) -> None:
        ops, runner = _ops(CommandResult(command="", ok=True))
        res = await ops.commit("   ", correlation_id=_CID)
        assert res.ok is False and res.error == "empty commit message"
        assert runner.calls == []

    async def test_message_is_shell_quoted(self) -> None:
        ops, runner = _ops(CommandResult(command="git commit", ok=True, stdout="[main deadbee] x\n"))
        await ops.commit("fix: `rm -rf /` & stuff", correlation_id=_CID)
        assert runner.calls[0][0] == f"git commit -m {shlex.quote('fix: `rm -rf /` & stuff')}"


class TestBranch:
    async def test_create_branch(self) -> None:
        ops, runner = _ops(CommandResult(command="git checkout", ok=True))
        res = await ops.create_branch("feature/x", correlation_id=_CID)
        assert res.ok is True and res.branch == "feature/x" and res.action == "branch"
        assert runner.calls[0][0] == f"git checkout -b {shlex.quote('feature/x')}"

    async def test_checkout_existing(self) -> None:
        ops, runner = _ops(CommandResult(command="git checkout", ok=True))
        res = await ops.checkout_branch("main", correlation_id=_CID)
        assert res.ok is True and res.branch == "main" and res.action == "checkout"
        assert runner.calls[0][0] == f"git checkout {shlex.quote('main')}"

    async def test_bad_branch_is_honest_failure(self) -> None:
        ops, _ = _ops(CommandResult(command="git checkout", ok=False, exit_code=128, stderr="fatal: bad name"))
        res = await ops.create_branch("bad name", correlation_id=_CID)
        assert res.ok is False and res.branch is None and res.error == "fatal: bad name"

    async def test_empty_name_is_error_no_run(self) -> None:
        ops, runner = _ops(CommandResult(command="", ok=True))
        res = await ops.create_branch("  ", correlation_id=_CID)
        assert res.ok is False and res.error == "empty branch name"
        assert runner.calls == []


class TestDenied:
    async def test_denied_folds_without_fabricating(self) -> None:
        ops, _ = _ops(CommandResult(command="git add -A", denied=True, error="denied: policy"))
        res = await ops.stage([], all_changes=True, correlation_id=_CID)
        assert res.denied is True and res.ok is False and res.error == "denied: policy"
