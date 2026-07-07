from pathlib import Path

import pytest

from atlas.app import build
from atlas.infra.types import ToolRequest
from atlas.safety.engine import DeniedError


@pytest.mark.asyncio
async def test_mass_deletion_hard_block(tmp_path: Path) -> None:
    # Set up some dummy files
    d = tmp_path / "target"
    d.mkdir()
    for i in range(30):
        (d / f"{i}.txt").write_text("a")
        
    atlas = await build()
    await atlas.db.start()
    
    # Inject mass deletion threshold of 25 (default)
    # The read_globs / write_globs aren't properly populated from settings in tests,
    # but we just want to ensure the guard blocks.
    tool = atlas.tools["filesystem"]
    args = {
        "operation": "delete",
        "path": str(d),
        "target_count": tool._count_delete_targets(str(d)) # type: ignore[attr-defined]
    }
    
    req = ToolRequest(
        correlation_id=atlas.ids.correlation_id(),
        tool="filesystem",
        operation="delete",
        args=args
    )
    
    with pytest.raises(DeniedError) as exc:
        await atlas.safety.guard(req, tool)
        
    assert "mass_deletion" in str(exc.value)
    
    await atlas.close()
