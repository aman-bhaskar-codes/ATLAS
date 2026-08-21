"""Environment detection (Phase 31) — ATLAS's self-model of available bodies.

WHY explicit detection: the planner must never pretend a substrate exists.
"Android device disconnected" / "accessibility permission missing" are facts
that enter WorldState so the planner can choose alternatives or explain the
limitation (Phases 45/46).

All probes are cheap, side-effect-free, and tolerate missing binaries — a
missing adb/playwright/pyobjc is a normal state, not an error.
"""

from __future__ import annotations

import asyncio
import shutil

from pydantic import BaseModel

from atlas.infra.logging import get_logger
from atlas.infra.platform import has_pyobjc, is_macos
from atlas.perception.contracts import Substrate

_log = get_logger("atlas.cu.env")


class SubstrateStatus(BaseModel):
    model_config = {"frozen": True}
    substrate: Substrate
    available: bool
    detail: str = ""
    permission_missing: str | None = None


class EnvironmentReport(BaseModel):
    """What ATLAS can currently perceive/act through. Fed to WorldState."""

    model_config = {"frozen": True}
    os: str
    substrates: tuple[SubstrateStatus, ...] = ()
    adb_path: str | None = None
    android_devices: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def available(self, substrate: Substrate) -> bool:
        return any(s.substrate == substrate and s.available for s in self.substrates)

    def limitation_for(self, substrate: Substrate) -> str | None:
        """Human-readable reason a substrate is unavailable (Phase 46)."""
        for s in self.substrates:
            if s.substrate == substrate and not s.available:
                return s.detail or f"{substrate.value} unavailable"
        return f"{substrate.value} not detected in this environment"


class EnvironmentDetector:
    """Detects available substrates. Probes are async-safe and bounded."""

    def __init__(self, *, adb_timeout_s: float = 3.0) -> None:
        self._adb_timeout_s = adb_timeout_s

    async def detect(self) -> EnvironmentReport:
        import platform

        statuses: list[SubstrateStatus] = []
        notes: list[str] = []

        # Local substrates are always present as surfaces (filesystem/shell)
        statuses.append(SubstrateStatus(substrate=Substrate.FILESYSTEM, available=True, detail="local filesystem"))
        statuses.append(SubstrateStatus(substrate=Substrate.SHELL, available=True, detail="local shell"))

        # macOS desktop control needs pyobjc (AX) or at least osascript
        if is_macos():
            if has_pyobjc():
                statuses.append(
                    SubstrateStatus(substrate=Substrate.MACOS, available=True, detail="pyobjc AX available")
                )
            else:
                statuses.append(
                    SubstrateStatus(
                        substrate=Substrate.MACOS,
                        available=True,  # osascript intents still work without pyobjc
                        detail="osascript-only (install the macos extra for AX perception)",
                        permission_missing="pyobjc",
                    )
                )
        else:
            statuses.append(SubstrateStatus(substrate=Substrate.MACOS, available=False, detail="not macOS"))

        # Browser: playwright installed?
        try:
            import playwright  # noqa: F401

            browser_ok = True
            browser_detail = "playwright installed"
        except ImportError:
            browser_ok = False
            browser_detail = "playwright not installed"
        statuses.append(SubstrateStatus(substrate=Substrate.BROWSER, available=browser_ok, detail=browser_detail))

        # Android via ADB
        adb_path = shutil.which("adb")
        devices: tuple[str, ...] = ()
        if adb_path:
            devices = await self._adb_devices(adb_path)
            statuses.append(
                SubstrateStatus(
                    substrate=Substrate.ANDROID,
                    available=bool(devices),
                    detail=f"{len(devices)} device(s) connected" if devices else "adb present, no devices",
                )
            )
        else:
            statuses.append(SubstrateStatus(substrate=Substrate.ANDROID, available=False, detail="adb not installed"))

        # API surface is always reachable (httpx) — the connector layer gates it
        statuses.append(SubstrateStatus(substrate=Substrate.API, available=True, detail="http connector"))

        report = EnvironmentReport(
            os=platform.system().lower(),
            substrates=tuple(statuses),
            adb_path=adb_path,
            android_devices=devices,
            notes=tuple(notes),
        )
        _log.info(
            "env.detected",
            event_type="computer_use",
            available=[s.substrate.value for s in statuses if s.available],
        )
        return report

    async def _adb_devices(self, adb_path: str) -> tuple[str, ...]:
        try:
            proc = await asyncio.create_subprocess_exec(
                adb_path,
                "devices",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=self._adb_timeout_s)
        except (OSError, TimeoutError):
            return ()
        devices: list[str] = []
        for line in out.decode(errors="replace").splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                devices.append(parts[0])
        return tuple(devices)
