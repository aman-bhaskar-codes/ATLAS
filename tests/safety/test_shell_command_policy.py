"""Regression tests for approval bypasses, using the real permission manifest."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

from atlas.infra.ids import CorrelationId
from atlas.infra.types import ToolRequest
from atlas.safety.classifier import TierClassifier
from atlas.safety.manifest import load_manifest
from atlas.safety.sandbox import SandboxResult
from atlas.tools.shell import ShellTool

CONFIG = Path(__file__).resolve().parents[2] / "config" / "permissions.yaml"


def _decision(command: str) -> str:
    manifest = load_manifest(yaml.safe_load(CONFIG.read_text()))
    classifier = TierClassifier(manifest, default_tier_on_error=2)
    return classifier.classify(
        ToolRequest(
            correlation_id=CorrelationId("shell-policy"), tool="shell", operation="run", args={"command": command}
        )
    ).decision


@pytest.mark.parametrize(
    "command",
    [
        "python3 -c 'print(1)'",
        "find /work -delete",
        "find /work -exec touch /work/new-file +",
        "find /work -fprint /work/results",
        "curl --output /work/result https://example.com",
        "wget https://example.com",
        "git diff --output=/work/diff",
        "git log --output=/work/log",
        "git diff --ext-diff",
        "git diff --textconv",
    ],
)
def test_code_execution_and_mutating_flags_require_confirmation(command: str) -> None:
    assert _decision(command) == "require_confirm"


@pytest.mark.parametrize(
    "command",
    ["cat ~/.ssh/id_rsa", "head ~/.aws/credentials", "cat .env", "cat -- .env.production", "cat -- ./secret.pem"],
)
def test_shell_cannot_read_credential_files(command: str) -> None:
    assert _decision(command) == "deny"


@pytest.mark.parametrize("command", ["catapult file", "git status-malicious", "python3 -completely-different"])
def test_command_prefixes_match_tokens_not_string_prefixes(command: str) -> None:
    assert _decision(command) != "allow"


@pytest.mark.parametrize("command", ["ls /work", "find /work -name '*.py'", "git status -s", "git diff --numstat"])
def test_actual_read_only_commands_remain_automatic(command: str) -> None:
    assert _decision(command) == "allow"


@pytest.mark.parametrize("command", ["python3 -c 'print(1)'", "curl https://example.com", "wget https://example.com"])
def test_confirmed_commands_are_still_executable(command: str) -> None:
    manifest = load_manifest(yaml.safe_load(CONFIG.read_text()))
    tool = ShellTool(
        read_only=manifest.allowed_commands["read_only"],
        side_effect=manifest.allowed_commands["side_effect"],
        sandbox=AsyncMock(),
        mounts={},
    )
    assert tool._allowed(command)[0]


@pytest.mark.parametrize("command", ["cat .env", "cat ~/.ssh/id_rsa"])
async def test_tool_defense_rejects_credentials_even_without_classifier(command: str) -> None:
    sandbox = AsyncMock()
    sandbox.run.return_value = SandboxResult(exit_code=0, stdout_tail="", stderr_tail="", duration_ms=1)
    tool = ShellTool(read_only=["cat"], side_effect=[], sandbox=sandbox, mounts={})
    result = await tool.execute({"command": command})
    assert not result.ok
    sandbox.run.assert_not_called()


async def test_workspace_relative_symlink_cannot_hide_a_credential(tmp_path: Path) -> None:
    secret = tmp_path / ".env"
    secret.write_text("synthetic-only")
    (tmp_path / "ordinary.txt").symlink_to(secret)
    manifest = load_manifest(yaml.safe_load(CONFIG.read_text()))
    decision = TierClassifier(manifest, default_tier_on_error=2).classify(
        ToolRequest(
            correlation_id=CorrelationId("relative-credential"),
            tool="shell",
            operation="run",
            args={"command": "cat ordinary.txt", "cwd": str(tmp_path)},
        )
    )
    assert decision.decision == "deny"
    sandbox = AsyncMock()
    sandbox.run.return_value = SandboxResult(exit_code=0, stdout_tail="synthetic-only", stderr_tail="", duration_ms=1)
    tool = ShellTool(read_only=["cat"], side_effect=[], sandbox=sandbox, mounts={str(tmp_path): "/work"})
    result = await tool.execute({"command": "cat ordinary.txt", "cwd": str(tmp_path)})
    assert not result.ok
    sandbox.run.assert_not_called()


async def test_container_mapped_path_cannot_hide_a_credential_symlink(tmp_path: Path) -> None:
    secret = tmp_path / ".env"
    secret.write_text("synthetic-only")
    (tmp_path / "ordinary.txt").symlink_to(secret)
    sandbox = AsyncMock()
    sandbox.run.return_value = SandboxResult(exit_code=0, stdout_tail="synthetic-only", stderr_tail="", duration_ms=1)
    tool = ShellTool(read_only=["cat"], side_effect=[], sandbox=sandbox, mounts={str(tmp_path): "/work"})
    result = await tool.execute({"command": "cat /work/ordinary.txt"})
    assert not result.ok
    sandbox.run.assert_not_called()


def test_positional_filename_with_equals_is_not_reinterpreted_as_option(tmp_path: Path) -> None:
    credentials = tmp_path / "private"
    credentials.mkdir()
    file = credentials / "config=prod"
    file.write_text("synthetic-only")
    tool = ShellTool(
        read_only=["cat"], side_effect=[], sandbox=AsyncMock(), mounts={}, credential_dirs=[str(credentials)]
    )
    assert not tool._allowed(f"cat {file}")[0]
