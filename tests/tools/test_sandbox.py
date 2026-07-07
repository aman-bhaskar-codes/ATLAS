from collections.abc import Sequence

import pytest

from atlas.safety.sandbox_docker import DockerSandbox, SandboxSpec


class FakeDockerRunner:
    def __init__(self) -> None:
        self.argv: Sequence[str] = []
        
    async def run(
        self, argv: Sequence[str], *, timeout_s: float, stdin: bytes | None = None
    ) -> tuple[int, str, str]:
        self.argv = argv
        return 0, "ok", ""

@pytest.mark.asyncio
async def test_docker_sandbox_argv_hardening() -> None:
    runner = FakeDockerRunner()
    spec = SandboxSpec(image="python:3.13-slim", network=False)
    sandbox = DockerSandbox(spec, runner=runner)
    
    await sandbox.run(["echo", "hello"], mounts={"/host": "/work"})
    
    argv = list(runner.argv)
    assert "--read-only" in argv
    assert "--cap-drop" in argv
    assert "ALL" in argv
    assert "--network" in argv
    assert "none" in argv
    assert "--volume" in argv
    assert "/host:/work" in argv
    assert argv[-3:] == ["python:3.13-slim", "echo", "hello"]
