"""macOS adapter tests — AppleScript rendering is pure + escapable; runner injected."""

from __future__ import annotations

from dataclasses import dataclass

from atlas.capabilities.computer_use.adapters.macos import MacOSControlAdapter, _escape
from atlas.control.contracts import ActionCapability, ControlAction
from atlas.perception.targets import TargetRef, TargetStrategy


@dataclass
class _Result:
    ok: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0


class _RecordingRunner:
    def __init__(self) -> None:
        self.scripts: list[str] = []

    async def run(self, script: str, *, timeout_s: float) -> _Result:
        self.scripts.append(script)
        return _Result(ok=True, exit_code=0)


def _adapter() -> tuple[MacOSControlAdapter, _RecordingRunner]:
    runner = _RecordingRunner()
    return MacOSControlAdapter(runner), runner


def test_escape_quotes_and_backslashes() -> None:
    assert _escape('say "hi"') == 'say \\"hi\\"'
    assert _escape("a\\b") == "a\\\\b"


async def test_launch_renders_activate() -> None:
    adapter, runner = _adapter()
    action = ControlAction(
        capability=ActionCapability.APP,
        operation="launch",
        arguments={"app": "TextEdit"},
    )
    result = await adapter.dispatch(action)
    assert result.ok is True
    assert runner.scripts == ['tell application "TextEdit" to activate']


async def test_click_escapes_label_injection() -> None:
    adapter, runner = _adapter()
    action = ControlAction(
        capability=ActionCapability.UI,
        operation="click",
        target=TargetRef(strategy=TargetStrategy.TEXT, value='Ok" & do shell script "rm -rf /" & "'),
        arguments={"app": "Notes"},
    )
    result = await adapter.dispatch(action)
    assert result.ok is True
    script = runner.scripts[0]
    # The malicious quote is escaped; no unescaped break-out exists.
    assert '\\" & do shell script' in script
    assert 'tell process "Notes"' in script


async def test_type_text_and_press_key_allowed_set() -> None:
    adapter, runner = _adapter()
    await adapter.dispatch(
        ControlAction(
            capability=ActionCapability.UI, operation="type_text", arguments={"app": "Notes", "text": "hello"}
        )
    )
    await adapter.dispatch(
        ControlAction(
            capability=ActionCapability.UI, operation="press_key", arguments={"app": "Notes", "key": "return"}
        )
    )
    assert 'keystroke "hello"' in runner.scripts[0]
    assert "key code 36" in runner.scripts[1]


async def test_press_key_rejects_unknown_key() -> None:
    adapter, _ = _adapter()
    result = await adapter.dispatch(
        ControlAction(
            capability=ActionCapability.UI,
            operation="press_key",
            arguments={"app": "Notes", "key": "format-disk"},
        )
    )
    assert result.ok is False


async def test_unknown_operation_is_rejected() -> None:
    adapter, runner = _adapter()
    result = await adapter.dispatch(
        ControlAction(capability=ActionCapability.UI, operation="delete_everything", arguments={"app": "Notes"})
    )
    assert result.ok is False
    assert runner.scripts == []  # nothing rendered, nothing ran


async def test_open_file_requires_app_and_path() -> None:
    adapter, runner = _adapter()
    result = await adapter.dispatch(
        ControlAction(capability=ActionCapability.APP, operation="open_file", arguments={"path": "/tmp/x.md"})
    )
    assert result.ok is False
    result2 = await adapter.dispatch(
        ControlAction(
            capability=ActionCapability.APP,
            operation="open_file",
            arguments={"app": "TextEdit", "path": "/tmp/x.md"},
        )
    )
    assert result2.ok is True
    assert 'POSIX file "/tmp/x.md"' in runner.scripts[0]
