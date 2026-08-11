"""Database and Vector Store Backups (Phase 11).

Automated backup routines to snapshot the SQLite WAL databases and ChromaDB 
vector stores to prevent data loss and ensure migration safety.
"""
import asyncio
import os
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from atlas.infra.config import Settings
from atlas.infra.logging import get_logger

_log = get_logger("atlas.backup")


async def create_backup(settings: Settings) -> str | None:
    """Create a compressed zip backup of the data_dir contents.
    
    Returns the path to the backup zip file if successful.
    """
    data_dir = settings.data_dir
    if not data_dir.exists():
        _log.warning("backup.skip", event_type="backup", detail="Data directory does not exist")
        return None
        
    backup_dir = data_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    
    now_ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"atlas_backup_{now_ts}.zip"
    
    # Run zip creation in a thread to avoid blocking the async event loop
    def _do_zip() -> str:
        with zipfile.ZipFile(backup_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(data_dir):
                # Don't backup the backups directory itself
                if "backups" in Path(root).parts:
                    continue
                for file in files:
                    file_path = Path(root) / file
                    if file_path.suffix == ".sock":
                        continue
                    # Relative path inside the zip
                    arcname = file_path.relative_to(data_dir)
                    zf.write(file_path, arcname)
        return str(backup_path)

    _log.info("backup.started", event_type="backup", path=str(backup_path))
    try:
        res = await asyncio.to_thread(_do_zip)
        _log.info("backup.completed", event_type="backup", path=res)
        return res
    except Exception as exc:
        _log.error("backup.failed", event_type="backup", error=str(exc))
        if backup_path.exists():
            try:
                backup_path.unlink()
            except OSError:
                pass
        return None
