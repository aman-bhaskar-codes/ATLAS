"""Android transport boundary — ADB as an injectable async subprocess runner.

WHY a transport protocol: the Android adapter must be testable on machines
without a device (CI). Tests inject a FakeAndroidTransport that returns
canned uiautomator dumps; production uses ADBTransport. The cognitive core
never sees ADB syntax.
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class TransportResult:
    ok: bool
    stdout: str
    stderr: str


class AndroidTransport(Protocol):
    async def shell(self, command: str, *, timeout_s: float = 15.0) -> TransportResult: ...

    async def is_connected(self) -> bool: ...


class ADBTransport:
    """Production transport over the ``adb`` CLI (async subprocess)."""

    def __init__(self, serial: str | None = None, adb_path: str = "adb") -> None:
        self._serial = serial
        self._adb = adb_path

    def _argv(self, *args: str) -> list[str]:
        base = [self._adb]
        if self._serial:
            base += ["-s", self._serial]
        return [*base, *args]

    async def shell(self, command: str, *, timeout_s: float = 15.0) -> TransportResult:
        if shutil.which(self._adb) is None:
            return TransportResult(False, "", "adb not found on PATH")
        proc = await asyncio.create_subprocess_exec(
            *self._argv("shell", command),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except TimeoutError:
            proc.kill()
            return TransportResult(False, "", f"adb shell timed out after {timeout_s}s")
        code = proc.returncode if proc.returncode is not None else -1
        return TransportResult(code == 0, out.decode(errors="replace").strip(), err.decode(errors="replace").strip())

    async def is_connected(self) -> bool:
        if shutil.which(self._adb) is None:
            return False
        proc = await asyncio.create_subprocess_exec(
            *self._argv("devices"),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        except TimeoutError:
            proc.kill()
            return False
        for line in out.decode(errors="replace").splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                if self._serial is None or parts[0] == self._serial:
                    return True
        return False


class NullAndroidTransport:
    """Honest no-device transport. Never fakes success."""

    async def shell(self, command: str, *, timeout_s: float = 15.0) -> TransportResult:
        return TransportResult(False, "", "no Android transport configured")

    async def is_connected(self) -> bool:
        return False
