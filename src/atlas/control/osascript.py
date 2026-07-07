"""osascript execution backend.

WHY a thin injectable runner: keeps the subprocess boundary in one testable
place and lets the tool inject a fake in tests (no real AppleScript in CI). We
pass the script via stdin (-) to avoid arg-length and quoting pitfalls, and we
always time out.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from atlas.infra.platform import is_macos


@dataclass(frozen=True)
class ScriptResult:
    ok: bool
    stdout: str
    stderr: str
    exit_code: int


class ScriptRunner(Protocol):
    async def run(self, script: str, *, timeout_s: float) -> ScriptResult: ...


class OsascriptRunner:
    async def run(self, script: str, *, timeout_s: float = 15.0) -> ScriptResult:
        if not is_macos():
            return ScriptResult(False, "", "osascript unavailable: not macOS", 127)
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(
                proc.communicate(script.encode()), timeout=timeout_s
            )
        except TimeoutError:
            proc.kill()
            return ScriptResult(False, "", f"timed out after {timeout_s}s", 124)
        code = proc.returncode if proc.returncode is not None else -1
        return ScriptResult(code == 0, out.decode().strip(), err.decode().strip(), code)
