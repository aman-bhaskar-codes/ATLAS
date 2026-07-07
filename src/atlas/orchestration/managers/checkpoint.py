"""Checkpoint manager — resumable execution.

WHY interface + simple default now: full resumable orchestration is a later
concern, but the SHAPE of a checkpoint must be fixed now so the loop can persist
it from day one. Default writes JSON to the data dir; a SQLite/remote store can
replace it behind the same protocol with zero loop changes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol


class Checkpoint(dict[str, Any]):
    """state, context, reasoning_history, tool_history, memory_refs, retries, meta."""


class CheckpointStore(Protocol):
    async def save(self, task_id: str, checkpoint: Checkpoint) -> None: ...
    async def load(self, task_id: str) -> Checkpoint | None: ...


class FileCheckpointStore:
    def __init__(self, data_dir: Path) -> None:
        self._dir = data_dir / "checkpoints"
        self._dir.mkdir(parents=True, exist_ok=True)

    async def save(self, task_id: str, checkpoint: Checkpoint) -> None:
        (self._dir / f"{task_id}.json").write_text(json.dumps(checkpoint, default=str))

    async def load(self, task_id: str) -> Checkpoint | None:
        p = self._dir / f"{task_id}.json"
        if not p.exists():
            return None
        return Checkpoint(json.loads(p.read_text()))
