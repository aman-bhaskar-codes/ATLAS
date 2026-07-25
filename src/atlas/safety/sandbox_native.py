"""Native (no-Docker) sandbox for dev/local use.

WHY: Docker is required for production isolation. In dev mode (ATLAS_ENV=dev)
the user often doesn't have Docker running. This sandbox runs commands directly
on the host inside the allowed mount paths — NO isolation, dev only. It is
intentionally rejected in production (env != 'dev').
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from atlas.infra.logging import get_logger
from atlas.safety.sandbox import SandboxResult

_log = get_logger("atlas.sandbox.native")
_MAX_OUTPUT = 16_000


class NativeSandbox:
    """Runs commands directly on the host. DEV ONLY — no isolation whatsoever."""

    def __init__(self, env: str = "dev") -> None:
        if env != "dev":
            raise RuntimeError("NativeSandbox is only permitted in dev environment")

    async def run(
        self,
        command: list[str],
        *,
        mounts: dict[str, str],
        network: bool = False,
        timeout_s: float = 60.0,
        stdin: bytes | None = None,
    ) -> SandboxResult:
        # Remap mount_target paths back to their host equivalents in the argv.
        # e.g. /work/answer.txt -> /Users/.../scratch/answer.txt
        remapped = []
        for arg in command:
            for host_path, container_path in mounts.items():
                if arg.startswith(container_path):
                    arg = arg.replace(container_path, host_path, 1)
                    break
            remapped.append(arg)

        _log.info(
            "sandbox.native.run", event_type="sandbox",
            cmd=remapped, network=network
        )

        # Ensure all mount source dirs exist.
        for host_path in mounts:
            Path(host_path).mkdir(parents=True, exist_ok=True)

        start = time.perf_counter()
        try:
            proc = await asyncio.create_subprocess_exec(
                *remapped,
                stdin=asyncio.subprocess.PIPE if stdin is not None else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ},
            )
            out_bytes, err_bytes = await asyncio.wait_for(
                proc.communicate(input=stdin), timeout=timeout_s
            )
            code = proc.returncode if proc.returncode is not None else -1
        except TimeoutError:
            return SandboxResult(
                exit_code=124, stdout_tail="",
                stderr_tail=f"timed out after {timeout_s}s",
                duration_ms=int((time.perf_counter() - start) * 1000),
            )
        except Exception as exc:  # noqa: BLE001
            return SandboxResult(
                exit_code=1, stdout_tail="",
                stderr_tail=str(exc),
                duration_ms=int((time.perf_counter() - start) * 1000),
            )

        dur = int((time.perf_counter() - start) * 1000)
        return SandboxResult(
            exit_code=code,
            stdout_tail=out_bytes.decode(errors="replace")[-_MAX_OUTPUT:],
            stderr_tail=err_bytes.decode(errors="replace")[-_MAX_OUTPUT:],
            duration_ms=dur,
        )

    async def health(self) -> bool:
        return True
