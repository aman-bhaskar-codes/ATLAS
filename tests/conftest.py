"""Test fixtures."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
import yaml

from atlas.infra.db import Database
from atlas.infra.config import LoggingCfg
from atlas.infra.logging import configure_logging


@pytest.fixture(scope="session", autouse=True)
def setup_logging() -> None:
    # Quiet the logs during tests unless we explicitly want them.
    configure_logging(LoggingCfg(level="WARNING", format="console"))


@pytest_asyncio.fixture
async def memory_db(tmp_path: Path) -> AsyncIterator[Database]:
    db_path = tmp_path / "test.db"
    db = Database(db_path)
    await db.start()
    yield db
    await db.stop()


@pytest.fixture
def manifest_dir(tmp_path: Path) -> Path:
    # A base permissive manifest for tests that don't need to specify their own
    data = {
        "version": 1,
        "allowed_paths": {"read": ["*"], "write": ["*"]},
        "allowed_commands": {"read_only": ["*"], "side_effect": ["*"]},
        "whatsapp": {"known_contacts": []},
        "safety": {"credential_dirs": ["/fake/ssh"], "mass_deletion_threshold": 5},
        "rules": [
            {"tool": "*", "operation": "*", "tier": 0}
        ],
        "hard_block": []
    }
    manifest_path = tmp_path / "permissions.yaml"
    manifest_path.write_text(yaml.dump(data))
    return tmp_path
