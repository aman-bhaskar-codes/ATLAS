"""Backups must be bounded in count and frequency.

THE BUG THIS PINS: ``create_backup`` ran on every boot, zipped the ENTIRE
``data_dir``, and kept every archive forever. Each new archive also compressed all
the previous ones' worth of growth, so the data directory grew super-linearly in
the number of restarts — on a dev machine that reloads often, unboundedly.

Two bounds now apply, both env-tunable, both asserted here: how many archives
survive (``ATLAS_BACKUP_KEEP``) and how often one is written at all
(``ATLAS_BACKUP_COOLDOWN_S``).

What these tests deliberately do NOT assert: that a restored archive is usable.
Zipping a live WAL database is not crash-consistent — see the module docstring in
``atlas/infra/backup.py``. Pretending otherwise with a round-trip test would
manufacture confidence the implementation does not earn.
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import pytest

from atlas.infra.backup import create_backup
from atlas.infra.config import Settings, load_settings

_ARCHIVES = "atlas_backup_*.zip"


def _settings(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Real Settings pointed at a scratch data dir with some content to archive."""
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "atlas.db").write_bytes(b"not really a database, but a real file")
    (data_dir / "atlas.db-wal").write_bytes(b"write-ahead log")
    monkeypatch.setenv("ATLAS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("ATLAS_ENV", "dev")
    return load_settings()


def _archive_names(data_dir: Path) -> list[str]:
    return sorted(p.name for p in (data_dir / "backups").glob(_ARCHIVES))


async def test_retention_keeps_only_the_configured_number(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Seven successive backups leave five archives."""
    data_dir = tmp_path / "data"
    monkeypatch.setenv("ATLAS_BACKUP_COOLDOWN_S", "0")
    monkeypatch.setenv("ATLAS_BACKUP_KEEP", "5")
    settings = _settings(data_dir, monkeypatch)

    written = [await create_backup(settings) for _ in range(7)]

    assert all(w is not None for w in written), "a backup was skipped with the cooldown disabled"
    assert len(_archive_names(data_dir)) == 5


async def test_retention_keeps_the_NEWEST_archives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deleting the newest instead of the oldest would be worse than not pruning."""
    data_dir = tmp_path / "data"
    monkeypatch.setenv("ATLAS_BACKUP_COOLDOWN_S", "0")
    monkeypatch.setenv("ATLAS_BACKUP_KEEP", "2")
    settings = _settings(data_dir, monkeypatch)

    written = [await create_backup(settings) for _ in range(4)]

    survivors = _archive_names(data_dir)
    assert len(survivors) == 2
    assert Path(str(written[-1])).name in survivors, "the most recent backup was pruned"
    assert Path(str(written[0])).name not in survivors, "the oldest backup survived"


async def test_the_cooldown_skips_a_redundant_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A restart loop must not produce one archive per restart."""
    data_dir = tmp_path / "data"
    monkeypatch.setenv("ATLAS_BACKUP_COOLDOWN_S", "3600")
    settings = _settings(data_dir, monkeypatch)

    first = await create_backup(settings)
    second = await create_backup(settings)

    assert first is not None
    assert second is None, "the cooldown did not suppress the second backup"
    assert len(_archive_names(data_dir)) == 1


async def test_a_malformed_keep_value_does_not_delete_everything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A typo must fall back to the default, never to "keep zero"."""
    data_dir = tmp_path / "data"
    monkeypatch.setenv("ATLAS_BACKUP_COOLDOWN_S", "0")
    monkeypatch.setenv("ATLAS_BACKUP_KEEP", "five")
    settings = _settings(data_dir, monkeypatch)

    result = await create_backup(settings)

    assert result is not None
    assert len(_archive_names(data_dir)) == 1, "the archive just written was deleted"


async def test_keep_is_clamped_to_at_least_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KEEP=0 would mean pruning the backup that was just taken."""
    data_dir = tmp_path / "data"
    monkeypatch.setenv("ATLAS_BACKUP_COOLDOWN_S", "0")
    monkeypatch.setenv("ATLAS_BACKUP_KEEP", "0")
    settings = _settings(data_dir, monkeypatch)

    await create_backup(settings)
    await create_backup(settings)

    assert len(_archive_names(data_dir)) == 1


async def test_the_archive_excludes_the_backup_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Otherwise each backup contains all the previous ones — exponential growth."""
    data_dir = tmp_path / "data"
    monkeypatch.setenv("ATLAS_BACKUP_COOLDOWN_S", "0")
    settings = _settings(data_dir, monkeypatch)

    await create_backup(settings)
    second = await create_backup(settings)
    assert second is not None

    with zipfile.ZipFile(second) as zf:
        names = zf.namelist()
    assert names, "the archive is empty"
    assert not any(n.startswith("backups/") for n in names), f"backups nested inside a backup: {names}"
    assert "atlas.db" in names and "atlas.db-wal" in names, (
        "the WAL sidecar must be included — a snapshot without it loses committed pages"
    )


async def test_a_missing_data_directory_is_a_skip_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Startup runs this fire-and-forget; a first boot must not log a failure."""
    data_dir = tmp_path / "data"
    monkeypatch.setenv("ATLAS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("ATLAS_ENV", "dev")
    settings = load_settings()
    # load_settings may create the directory; remove it to reach the branch.
    shutil.rmtree(data_dir, ignore_errors=True)
    assert not data_dir.exists()

    assert await create_backup(settings) is None
