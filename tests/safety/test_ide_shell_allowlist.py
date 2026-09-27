"""IDE verbs ↔ shell allowlist parity — the "no dead verb" pin.

WHY: `CommandRunner` sends every IDE command through `SafetyEngine.guard` and then the
REAL `ShellTool`, whose token-prefix allowlist (`config/permissions.yaml` →
`allowed_commands`) is a DENY-BY-DEFAULT gate. Every other IDE test injects a fake
command tool, so a verb whose command sits in NEITHER bucket stays green in CI while
being dead in production: the classifier raises it to CONFIRM, the user approves — and
the tool still answers "not in allowlist", so nothing ever runs.

WHAT: drives the REAL engines (`GitEngine`, `GitOps`, `GitCheckpoints`, `TestRunner`,
`DiagnosticsCollector`, `analyze_project`) over a recording funnel to record every
command string the IDE can actually issue, then holds each one against the REAL
manifest + `ShellTool` + `TierClassifier`:
  * the tool gate accepts it (no dead verb), and
  * the classifier tiers it exactly as the policy table declares — pure git reads
    auto-approve; every git write and every command that runs project code confirms.

The table is exhaustive on purpose: when an IDE verb starts issuing a new command, this
suite fails until its tier is an explicit decision instead of an accident.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import yaml

from atlas.capabilities.ide.checkpoints import GitCheckpoints
from atlas.capabilities.ide.commands import CommandRunner
from atlas.capabilities.ide.contracts import WorkspaceId, WorkspaceRef
from atlas.capabilities.ide.diagnostics import DiagnosticsCollector
from atlas.capabilities.ide.git import GitEngine
from atlas.capabilities.ide.git_ops import GitOps
from atlas.capabilities.ide.project import analyze_project
from atlas.capabilities.ide.tests import (
    TestRunner as Runner,
)
from atlas.capabilities.ide.workspace import WorkspaceEngine
from atlas.infra.config import SafetyCfg
from atlas.infra.ids import CorrelationId
from atlas.infra.types import Tier, ToolRequest, ToolResult
from atlas.safety.audit import AuditLog
from atlas.safety.classifier import TierClassifier
from atlas.safety.engine import SafetyEngine
from atlas.safety.manifest import Manifest, load_manifest
from atlas.safety.policy import KillSwitchPolicy, PolicyEngine
from atlas.tools.shell import ShellTool
from tests.fakes import FakeClock, FakeConfirmer, FakeKillSwitch

CONFIG = Path(__file__).resolve().parents[2] / "config" / "permissions.yaml"
_CID = CorrelationId("cid-parity")

# The four families `parse_diagnostics` normalizes, with the invocation a caller (agent
# or human) sends to the diagnostics verb. `tsc --noEmit` is the typecheck-only form.
_DIAGNOSTIC_COMMANDS: tuple[str, ...] = ("mypy .", "ruff check .", "eslint .", "tsc --noEmit")


def _canned_stdout(command: str) -> str:
    """Plausible output so each engine walks far enough to build its NEXT command (a
    snapshot only resolves HEAD once `git stash create` has printed a sha)."""
    if command.startswith("git stash create") or command.startswith("git rev-parse"):
        return "deadbeefcafe\n"
    if command.startswith("git for-each-ref"):
        return "refs/atlas/checkpoints/cp1\tdeadbee\tbefore edit\n"
    if command.startswith("git status"):
        return "## main...origin/main\n M a.py\n"
    if command.startswith("git diff"):
        return "1\t1\ta.py\n"
    return "ok\n"


class _RecordingShell:
    """A `Tool` that records the command and answers from `_canned_stdout`. Nothing is
    executed — the point is to learn the command STRINGS, not to run them."""

    name = "shell"

    def __init__(self) -> None:
        self.commands: list[str] = []

    def dry_run(self, args: dict[str, Any]) -> str:
        return f"RUN {args.get('command')!r}"

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        command = str(args.get("command", ""))
        self.commands.append(command)
        return ToolResult(
            ok=True,
            output={"exit_code": 0, "stdout": _canned_stdout(command), "stderr": "", "duration_ms": 1},
        )


class _RecordingSafety:
    """Stands in for `SafetyEngine` only to hand the request to the recording tool. The
    real classifier is exercised separately, against the real manifest."""

    def __init__(self) -> None:
        self.requests: list[ToolRequest] = []

    async def guard(self, req: ToolRequest, tool: Any) -> ToolResult:
        self.requests.append(req)
        return await tool.execute(req.args)


def _polyglot_root(tmp_path: Path) -> Path:
    """One root that trips every detector in `analyze_project`, so the candidates the
    IDE reports — and then asks the shell tool to run — are all in the inventory."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\nversion = "0.0.0"\n')
    (tmp_path / "conftest.py").write_text("")  # claims pytest without needing the dep
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "vitest run"}}))
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "demo"\nversion = "0.0.0"\n')
    (tmp_path / "go.mod").write_text("module demo\n")
    return tmp_path


def _workspace_ref(root: Path) -> WorkspaceRef:
    return WorkspaceRef(
        id=WorkspaceId("ws-parity"),
        name="parity",
        root_paths=(str(root),),
        created_ts="2026-01-01T00:00:00Z",
        last_opened_ts="2026-01-01T00:00:00Z",
    )


async def _issued_commands(root: Path) -> list[str]:
    """Every command string the IDE verbs can put through the funnel, learned by driving
    the real engines (fakes answer the funnel; the strings are the real ones)."""
    tool = _RecordingShell()
    runner = CommandRunner(_RecordingSafety(), tool)  # type: ignore[arg-type]

    engine = GitEngine(runner, str(root))
    await engine.status(correlation_id=_CID)
    await engine.diff(correlation_id=_CID)
    await engine.diff(staged=True, correlation_id=_CID)

    ops = GitOps(runner, str(root))
    await ops.stage([], all_changes=True, correlation_id=_CID)
    await ops.stage(["a.py"], correlation_id=_CID)
    await ops.unstage([], all_changes=True, correlation_id=_CID)
    await ops.unstage(["a.py"], correlation_id=_CID)
    await ops.commit("parity", correlation_id=_CID)
    await ops.create_branch("feat/parity", correlation_id=_CID)
    await ops.checkout_branch("main", correlation_id=_CID)

    cps = GitCheckpoints(runner, str(root))
    await cps.snapshot("cp1", label="before edit", correlation_id=_CID)
    await cps.restore("cp1", correlation_id=_CID)
    await cps.list(correlation_id=_CID)

    model = analyze_project(WorkspaceEngine(_workspace_ref(root)))
    for command in (*model.test_commands, *_DIAGNOSTIC_COMMANDS):
        await Runner(runner).run(command, cwd=str(root), correlation_id=_CID)
        await DiagnosticsCollector(runner).collect(command, cwd=str(root), correlation_id=_CID)
    return tool.commands


# The declared policy, mirroring README's "the governed-command path is real": pure git
# reads auto-approve; every git WRITE and every command that runs project code stays
# executable but confirm-gated.
_ALLOW: tuple[str, ...] = ("git status", "git diff", "git rev-parse", "git for-each-ref")
_REQUIRE_CONFIRM: tuple[str, ...] = (
    "git add",
    "git commit",
    "git restore",
    "git checkout",
    "git update-ref",
    "git stash",
    "pytest",
    "mypy",
    "ruff",
    "eslint",
    "tsc",
    "cargo test",
    "go test",
    "npm test",
    "npm run test",
    "pnpm test",
    "yarn test",
    "bun test",
)


def _declared(command: str) -> str:
    """The decision the policy table declares for `command`. Refuses to default: an
    undecided command is a failure, not an implicit allow."""
    for prefix in _REQUIRE_CONFIRM:
        if command.startswith(prefix):
            return "require_confirm"
    for prefix in _ALLOW:
        if command.startswith(prefix):
            return "allow"
    raise AssertionError(f"undeclared IDE command {command!r}: give it an explicit tier")


def _manifest_and_tool() -> tuple[Manifest, ShellTool]:
    """The REAL manifest wired into the REAL tool gate, exactly as `app.py` builds it."""
    manifest = load_manifest(yaml.safe_load(CONFIG.read_text()))
    tool = ShellTool(
        read_only=manifest.allowed_commands.get("read_only", []),
        side_effect=manifest.allowed_commands.get("side_effect", []),
        sandbox=AsyncMock(),  # never reached: only the allowlist gate is exercised
        mounts={},
    )
    return manifest, tool


def _classify(manifest: Manifest, command: str) -> str:
    classifier = TierClassifier(manifest, default_tier_on_error=int(Tier.CONFIRM))
    decision = classifier.classify(
        ToolRequest(correlation_id=_CID, tool="shell", operation="run", args={"command": command})
    )
    return decision.decision


class TestFunnelAllowlistParity:
    """No IDE verb may be dead, and none may be silently AUTO when it writes."""

    async def test_no_ide_command_is_dead_against_the_real_tool_gate(self, tmp_path: Path) -> None:
        _, tool = _manifest_and_tool()
        for command in await _issued_commands(_polyglot_root(tmp_path)):
            allowed, reason = tool._allowed(command)
            assert allowed, f"dead verb: ShellTool refuses {command!r} ({reason})"

    async def test_classifier_tiers_every_ide_command_as_declared(self, tmp_path: Path) -> None:
        manifest, _ = _manifest_and_tool()
        for command in await _issued_commands(_polyglot_root(tmp_path)):
            assert _classify(manifest, command) == _declared(command), command

    async def test_inventory_covers_every_verb_family(self, tmp_path: Path) -> None:
        issued = await _issued_commands(_polyglot_root(tmp_path))
        required = (
            "git status --porcelain=v1 --branch",
            "git diff --numstat",
            "git diff --staged",
            "git add -A",
            "git restore --staged -- .",
            "git commit -m parity",
            "git checkout -b feat/parity",
            "git checkout main",
            "git stash create",
            "git rev-parse --verify --quiet",
            "git update-ref",
            "git for-each-ref",
            "pytest",
            "mypy .",
            "ruff check .",
            "eslint .",
            "tsc --noEmit",
            "cargo test",
            "go test ./...",
            "npm run test",
        )
        for wanted in required:
            assert any(c.startswith(wanted) for c in issued), f"inventory lost {wanted!r}"
        assert {_declared(c) for c in issued} == {"allow", "require_confirm"}


class TestTheDenyPath:
    """README's rule for a new safety rule: prove the DENY path, not just the allow path."""

    def test_remote_writes_are_refused_by_the_tool_gate(self) -> None:
        _, tool = _manifest_and_tool()
        allowed, reason = tool._allowed("git push origin main")
        assert allowed is False and "not in allowlist" in reason

    def test_unknown_executable_is_refused_by_the_tool_gate(self) -> None:
        _, tool = _manifest_and_tool()
        allowed, reason = tool._allowed("rm -rf build")
        assert allowed is False and "not in allowlist" in reason

    async def test_an_approved_remote_write_still_cannot_run(self, memory_db: Any) -> None:
        # The classifier only raises `git push` to CONFIRM (the allowlist constraint is
        # violated) — so drive the REAL engine with a confirmer that approves EVERYTHING
        # and prove the tool's own gate is the backstop: nothing is ever executed. That
        # is the deliberate boundary, pinned end to end.
        manifest = load_manifest(yaml.safe_load(CONFIG.read_text()))
        sandbox = AsyncMock()
        tool = ShellTool(
            read_only=manifest.allowed_commands.get("read_only", []),
            side_effect=manifest.allowed_commands.get("side_effect", []),
            sandbox=sandbox,
            mounts={},
        )
        killswitch = FakeKillSwitch(active=False)
        engine = SafetyEngine(
            classifier=TierClassifier(manifest, default_tier_on_error=int(Tier.CONFIRM)),
            policy=PolicyEngine((KillSwitchPolicy(killswitch),)),  # type: ignore[arg-type]
            audit=AuditLog(memory_db),
            killswitch=killswitch,  # type: ignore[arg-type]
            clock=FakeClock(datetime.datetime.now()),  # type: ignore[arg-type]
            cfg=SafetyCfg(),
            confirmer=FakeConfirmer(response=True),  # approves everything
        )
        request = ToolRequest(
            correlation_id=_CID, tool="shell", operation="run", args={"command": "git push origin main"}
        )

        assert _classify(manifest, "git push origin main") == "require_confirm"
        result = await engine.guard(request, tool)  # type: ignore[arg-type]

        assert result.ok is False and "not in allowlist" in (result.error or "")
        sandbox.run.assert_not_called()  # approved, yet nothing ran
