from pathlib import Path

from atlas.safety.sandbox_docker import SandboxResult  # type: ignore
from atlas.tools.filesystem import FilesystemTool


class DummySandbox:
    async def run(
        self, command: list[str], *, mounts: dict[str, str],
        network: bool = False, timeout_s: float = 60.0,
        stdin: bytes | None = None
    ) -> SandboxResult:
        return SandboxResult(0, "mock_out", "", 10)

def test_filesystem_dry_run_delete_count(tmp_path: Path) -> None:
    tool = FilesystemTool(read_globs=[], write_globs=[], sandbox=DummySandbox())
    
    d = tmp_path / "target"
    d.mkdir()
    (d / "a.txt").write_text("a")
    (d / "b.txt").write_text("b")
    
    # Check counts
    assert tool._count_delete_targets(str(d)) == 2
    
    # Ensure dry_run sets count
    out = tool.dry_run({"operation": "delete", "path": str(d)})
    assert "2 item" in out
