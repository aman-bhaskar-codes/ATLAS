"""Data-directory snapshots.

Zips everything under ``data_dir`` (except the backup directory itself) into
``data_dir/backups/atlas_backup_<ts>.zip``.

CONSISTENCY — read this before trusting a restore
-------------------------------------------------
This is a file-level copy of a LIVE SQLite database in WAL mode, and it is
**not crash-consistent**. ``zipfile`` reads ``atlas.db``, then ``atlas.db-wal``,
then ``atlas.db-shm`` at three different instants; a checkpoint or a commit
landing between those reads produces an archive whose pages and write-ahead log
disagree. Recovery from such an archive can fail or silently lose the most recent
transactions. It is a useful "someone deleted the data directory" backstop and
nothing more.

The correct upgrade is SQLite's own ``VACUUM INTO '<path>'``, which takes a read
transaction and writes a fully consistent single-file copy of a live database with
no WAL sidecar. That is a behaviour change to the DB layer (no such helper exists
on ``Database`` today), so it is recorded as debt rather than half-implemented
here — see ``docs/final/TECHNICAL_DEBT_FINAL.md``.

RETENTION
---------
``create_backup`` used to run on every single boot and keep everything, so the
data directory grew by a full compressed copy of itself per start — and each new
copy included all the space the previous ones consumed being reported as free.
Two bounds now apply, both env-tunable:

* ``ATLAS_BACKUP_KEEP`` (default 5) — how many archives survive; older ones are
  deleted after a successful write.
* ``ATLAS_BACKUP_COOLDOWN_S`` (default 3600) — skip entirely when a backup was
  written this recently. Set to 0 to disable, which is what tests do.
"""

from __future__ import annotations

import asyncio
import os
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from atlas.infra.config import Settings
from atlas.infra.logging import get_logger

_log = get_logger("atlas.backup")

_ENV_KEEP = "ATLAS_BACKUP_KEEP"
_ENV_COOLDOWN_S = "ATLAS_BACKUP_COOLDOWN_S"
_DEFAULT_KEEP = 5
_DEFAULT_COOLDOWN_S = 3600.0

_ARCHIVE_GLOB = "atlas_backup_*.zip"


def _env_number(name: str, default: float, *, minimum: float) -> float:
    """Read a numeric env var, falling back to `default` on anything unparseable.

    A typo in an env var must never stop a backup from happening, and must never
    be interpreted as "keep zero archives".
    """
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return max(minimum, float(raw))
    except ValueError:
        _log.warning("backup.env_invalid", event_type="backup", var=name)
        return default


def _archives(backup_dir: Path) -> list[Path]:
    """Existing archives, oldest first.

    Sorted by mtime rather than by filename, because the timestamp in the name has
    only one-second resolution. The name is the tiebreak for two archives that
    somehow share an mtime, which is meaningful only because `_next_path` keeps
    suffixes monotonic within a second.
    """
    return sorted(backup_dir.glob(_ARCHIVE_GLOB), key=lambda p: (p.stat().st_mtime, p.name))


def _next_path(backup_dir: Path, stamp: str) -> Path:
    """An archive path for this second that no archive in this directory has used.

    WHY not just "the first name that does not exist": pruning deletes the OLDEST
    archives, which frees up the earliest names. A first-free-slot scan therefore
    hands out the name of an archive that was deleted a moment ago, so two
    different snapshots taken at different times share a filename and every log
    line naming one of them becomes ambiguous. Counting up from the highest suffix
    still present keeps names monotonic for as long as any archive from this second
    survives.
    """
    base = f"atlas_backup_{stamp}"
    siblings = list(backup_dir.glob(f"{base}*.zip"))
    if not siblings:
        return backup_dir / f"{base}.zip"
    highest = 0
    for path in siblings:
        tail = path.stem.removeprefix(base)
        if tail.startswith("_") and tail[1:].isdigit():
            highest = max(highest, int(tail[1:]))
    return backup_dir / f"{base}_{highest + 1:03d}.zip"


def _within_cooldown(backup_dir: Path, cooldown_s: float) -> Path | None:
    """The most recent archive if it is younger than `cooldown_s`, else None."""
    if cooldown_s <= 0:
        return None
    existing = _archives(backup_dir)
    if not existing:
        return None
    newest = existing[-1]
    return newest if (time.time() - newest.stat().st_mtime) < cooldown_s else None


def _prune(backup_dir: Path, keep: int) -> int:
    """Delete all but the `keep` newest archives. Returns how many were removed."""
    existing = _archives(backup_dir)
    doomed = existing[: max(0, len(existing) - keep)]
    removed = 0
    for path in doomed:
        try:
            path.unlink()
            removed += 1
        except OSError as exc:
            # A file we cannot delete is not a reason to fail a completed backup.
            _log.warning("backup.prune_failed", event_type="backup", path=str(path), error=str(exc))
    return removed


async def create_backup(settings: Settings) -> str | None:
    """Create a compressed zip backup of the data_dir contents.

    Returns the path to the backup zip, or None when nothing was written — which
    covers three distinct cases, all logged: no data directory, a recent enough
    backup already exists, or the write failed.
    """
    data_dir = settings.data_dir
    if not data_dir.exists():
        _log.warning("backup.skip", event_type="backup", detail="Data directory does not exist")
        return None

    backup_dir = data_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    keep = int(_env_number(_ENV_KEEP, _DEFAULT_KEEP, minimum=1))
    cooldown_s = _env_number(_ENV_COOLDOWN_S, _DEFAULT_COOLDOWN_S, minimum=0)

    recent = _within_cooldown(backup_dir, cooldown_s)
    if recent is not None:
        _log.info(
            "backup.skip",
            event_type="backup",
            detail="a recent backup already exists",
            cooldown_s=cooldown_s,
            existing=recent.name,
        )
        return None

    # Second-resolution timestamps collide when two backups land in the same second
    # (a test loop, or two workers starting together), so the name may carry a
    # suffix — see `_next_path` for why it is not simply the first free slot.
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    backup_path = _next_path(backup_dir, stamp)

    # Run zip creation in a thread to avoid blocking the async event loop
    def _do_zip() -> str:
        with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(data_dir):
                # Never recurse into our own output — checked relative to data_dir
                # so a data_dir that merely happens to sit under some other path
                # named "backups" is still archived.
                rel_root = Path(root).relative_to(data_dir)
                if rel_root.parts and rel_root.parts[0] == "backups":
                    continue
                for file in files:
                    file_path = Path(root) / file
                    if file_path.suffix == ".sock":
                        continue
                    # Relative path inside the zip
                    zf.write(file_path, rel_root / file)
        return str(backup_path)

    _log.info("backup.started", event_type="backup", path=str(backup_path))
    try:
        res = await asyncio.to_thread(_do_zip)
    except Exception as exc:
        _log.error("backup.failed", event_type="backup", error=str(exc))
        if backup_path.exists():
            try:
                backup_path.unlink()
            except OSError:
                pass
        return None

    # Prune only after a success: a failed write must never be the reason an old,
    # good archive is deleted.
    removed = _prune(backup_dir, keep)
    _log.info("backup.completed", event_type="backup", path=res, keep=keep, pruned=removed)
    return res
