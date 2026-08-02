"""Best-effort backup mechanics for the JSON memory partition."""

from __future__ import annotations

import datetime
import shutil
import stat
from pathlib import Path

from agent.memory.path_safety import reject_link_like


def copy_memory_backup(
    source: Path,
    backup_dir: Path,
    *,
    max_backups: int,
) -> None:
    """Copy one regular source without following a link-like backup endpoint."""

    source_inspection = reject_link_like(source)
    if not source_inspection.exists:
        return

    backup_inspection = reject_link_like(backup_dir)
    if (
        backup_inspection.exists
        and backup_inspection.metadata is not None
        and not stat.S_ISDIR(backup_inspection.metadata.st_mode)
    ):
        raise NotADirectoryError(f"Diretório de backup inválido: {backup_dir}")
    backup_dir.mkdir(parents=True, exist_ok=True)
    reject_link_like(backup_dir)

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"{source.name}.{timestamp}.bak"
    reject_link_like(backup_path)
    shutil.copy2(source, backup_path)

    backups = sorted(
        entry.name
        for entry in backup_dir.iterdir()
        if entry.name.startswith(source.name) and entry.name.endswith(".bak")
    )
    while len(backups) > max_backups:
        (backup_dir / backups.pop(0)).unlink()


__all__ = ["copy_memory_backup"]
