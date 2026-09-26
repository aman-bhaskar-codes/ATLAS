"""Workspace checkpoints — snapshot / restore (Slice 10).

A checkpoint captures the workspace's current working state so an agent (or the
human) can roll a risky change back. Like every IDE mutation, `GitCheckpoints`
owns NO execution: it delegates to `CommandRunner.run` (the single
`SafetyEngine.guard` funnel), so snapshot/restore is governed exactly as any
other shell dispatch — never a side door around ATLAS policy (Constitution). The
engine never shells out itself.

MECHANISM: a snapshot is `git stash create` (a dangling commit capturing the
working tree + index WITHOUT touching either), pinned under a hidden ref
namespace `refs/atlas/checkpoints/<id>` so it never pollutes branches, the stash
list, or the log, yet survives across processes. A clean tree has nothing to
stash, so the checkpoint pins HEAD instead (`clean=True`). Restore replays the
snapshot tree onto the working tree with `git checkout <sha> -- .`.

SECURITY: every value that enters a command string (the ref, the label, the
resolved sha) is `shlex.quote`d, so nothing is interpolated raw.

HONESTY: `ok`/`error` reflect the REAL git process; `denied` means the funnel
refused and nothing ran; an unknown/invalid checkpoint id is git's own non-zero
exit (or an empty rev-parse) surfaced as `ok=False` — never a fabricated success.
"""

from __future__ import annotations

import shlex

from atlas.capabilities.ide.commands import CommandRunner
from atlas.capabilities.ide.contracts import CheckpointRef, CheckpointResult, CommandResult
from atlas.infra.ids import CorrelationId

_DETAIL_LIMIT = 500
_CHECKPOINT_NS = "refs/atlas/checkpoints"


def _clip(text: str) -> str:
    text = text.strip()
    return text if len(text) <= _DETAIL_LIMIT else text[:_DETAIL_LIMIT] + "…"


def _fail(action: str, result: CommandResult) -> CheckpointResult:
    """Fold a non-ok / denied `CommandResult` into an honest failed checkpoint."""
    if result.denied:
        return CheckpointResult(action=action, denied=True, error=result.error)
    msg = result.stderr or result.stdout or result.error or "git command failed"
    return CheckpointResult(action=action, ok=False, error=_clip(msg))


class GitCheckpoints:
    """Governed snapshot/restore over one workspace root. Holds a `CommandRunner`
    (the same funnel every IDE command uses) + the workspace root. Stateless beyond
    that — one instance per resolved workspace."""

    def __init__(self, runner: CommandRunner, root: str) -> None:
        self._runner = runner
        self._root = root

    async def _run(self, command: str, *, correlation_id: CorrelationId) -> CommandResult:
        return await self._runner.run(command, cwd=self._root, correlation_id=correlation_id)

    async def snapshot(self, checkpoint_id: str, *, label: str = "", correlation_id: CorrelationId) -> CheckpointResult:
        """Capture the current working tree as a checkpoint pinned under
        `refs/atlas/checkpoints/<checkpoint_id>`. A clean tree has nothing to stash,
        so the checkpoint pins HEAD (`clean=True`). The stash-create commit does NOT
        mutate the working tree or index — the snapshot is side-effect free."""
        created = await self._run("git stash create", correlation_id=correlation_id)
        if not created.ok:
            return _fail("snapshot", created)
        sha = created.stdout.strip()
        clean = not sha
        if clean:
            head = await self._run("git rev-parse HEAD", correlation_id=correlation_id)
            if not head.ok:
                return _fail("snapshot", head)
            sha = head.stdout.strip()

        ref = f"{_CHECKPOINT_NS}/{checkpoint_id}"
        message = label or f"atlas checkpoint {checkpoint_id}"
        command = f"git update-ref -m {shlex.quote(message)} {shlex.quote(ref)} {shlex.quote(sha)}"
        stored = await self._run(command, correlation_id=correlation_id)
        if not stored.ok:
            return _fail("snapshot", stored)
        return CheckpointResult(
            action="snapshot",
            ok=True,
            checkpoint_id=checkpoint_id,
            commit=sha[:10],
            label=label,
            clean=clean,
            detail="captured clean tree at HEAD" if clean else "captured working tree",
        )

    async def restore(self, checkpoint_id: str, *, correlation_id: CorrelationId) -> CheckpointResult:
        """Restore the working tree to a previously captured checkpoint. Resolves
        the hidden ref to its sha, then replays that tree with `git checkout <sha>
        -- .`. An unknown/invalid id is an honest `ok=False`, never a fake success.

        NOTE: this replays tracked paths from the snapshot; it does not delete files
        created after the snapshot — an honest, non-destructive restore."""
        ref = f"{_CHECKPOINT_NS}/{checkpoint_id}"
        resolved = await self._run(f"git rev-parse --verify --quiet {shlex.quote(ref)}", correlation_id=correlation_id)
        if resolved.denied:
            return CheckpointResult(action="restore", denied=True, error=resolved.error)
        sha = resolved.stdout.strip()
        if not resolved.ok or not sha:
            return CheckpointResult(
                action="restore",
                ok=False,
                checkpoint_id=checkpoint_id,
                error=f"unknown checkpoint {checkpoint_id}",
            )
        applied = await self._run(f"git checkout {shlex.quote(sha)} -- .", correlation_id=correlation_id)
        if not applied.ok:
            return _fail("restore", applied)
        return CheckpointResult(
            action="restore",
            ok=True,
            checkpoint_id=checkpoint_id,
            commit=sha[:10],
            detail="restored working tree",
        )

    async def list(self, *, correlation_id: CorrelationId) -> tuple[CheckpointRef, ...]:
        """List the workspace's stored checkpoints (newest ref first is not
        guaranteed by git; callers sort if they care). A non-git root / no
        checkpoints yields an honest empty tuple, never a fabricated entry."""
        command = (
            f"git for-each-ref --format=%(refname)%09%(objectname:short)%09%(subject) {shlex.quote(_CHECKPOINT_NS)}"
        )
        result = await self._run(command, correlation_id=correlation_id)
        if not result.ok:
            return ()
        return _parse_refs(result.stdout)


def _parse_refs(output: str) -> tuple[CheckpointRef, ...]:
    """Parse `git for-each-ref` tab-separated lines into `CheckpointRef`s. The id is
    the ref's last path segment (the namespace prefix stripped)."""
    prefix = f"{_CHECKPOINT_NS}/"
    refs: list[CheckpointRef] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        refname, commit = parts[0], parts[1]
        subject = parts[2] if len(parts) > 2 else ""
        checkpoint_id = refname[len(prefix) :] if refname.startswith(prefix) else refname
        refs.append(CheckpointRef(checkpoint_id=checkpoint_id, commit=commit, label=subject))
    return tuple(refs)
