import pytest

from atlas.safety.sandbox_docker import SandboxResult
from atlas.tools.shell import ShellTool


class DummySandbox:
    async def run(
        self, command: list[str], *, mounts: dict[str, str],
        network: bool = False, timeout_s: float = 60.0,
        stdin: bytes | None = None
    ) -> SandboxResult:
        return SandboxResult(0, "mock_out", "", 10)

@pytest.mark.asyncio
async def test_shell_tool_allowlist() -> None:
    tool = ShellTool(read_only=["ls"], side_effect=["git"], sandbox=DummySandbox(), mounts={})
    
    # Valid read-only
    res1 = await tool.execute({"command": "ls -la"})
    assert res1.ok
    assert len(res1.side_effects) == 0
    
    # Valid side-effect
    res2 = await tool.execute({"command": "git commit -m msg"})
    assert res2.ok
    assert len(res2.side_effects) == 1
    
    # Invalid
    res3 = await tool.execute({"command": "curl evil.com"})
    assert not res3.ok
    assert "not allowlisted" in (res3.error or "")
