from collections.abc import Sequence

import pytest

from atlas.safety.sandbox_docker import DockerSandbox, SandboxSpec


class FakeDockerRunner:
    def __init__(self) -> None:
        self.argv: Sequence[str] = []

    async def run(self, argv: Sequence[str], *, timeout_s: float, stdin: bytes | None = None) -> tuple[int, str, str]:
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


@pytest.mark.asyncio
async def test_docker_sandbox_maps_a_cwd_under_a_mount() -> None:
    """The ADE names a HOST workspace root; inside the container it must become the
    mounted path, so the command runs in the workspace, not the image's own workdir."""
    runner = FakeDockerRunner()
    sandbox = DockerSandbox(SandboxSpec(image="python:3.13-slim"), runner=runner)

    await sandbox.run(["pytest"], mounts={"/host": "/work"}, cwd="/host/proj")

    argv = list(runner.argv)
    assert argv[argv.index("--workdir") + 1] == "/work/proj"


@pytest.mark.asyncio
async def test_docker_sandbox_ignores_a_cwd_outside_any_mount() -> None:
    # The container cannot see that directory, so the spec workdir wins: isolation is
    # never widened by a request, and the command is not silently run somewhere else.
    runner = FakeDockerRunner()
    sandbox = DockerSandbox(SandboxSpec(image="python:3.13-slim"), runner=runner)

    await sandbox.run(["pytest"], mounts={"/host": "/work"}, cwd="/etc")

    argv = list(runner.argv)
    assert argv[argv.index("--workdir") + 1] == "/work"
