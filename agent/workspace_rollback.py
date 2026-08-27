"""Small rollback operations used by :class:`WorkspaceManager`."""

from __future__ import annotations

import shutil
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any


def rollback_transactions(transactions: Iterable[Any], logger: Any) -> bool:
    success = True
    for transaction in reversed(tuple(transactions)):
        rollback = getattr(transaction, "rollback", None)
        if not callable(rollback):
            success = False
            continue
        try:
            if rollback() is False:
                success = False
                logger.error(
                    "Rollback de transacao incompleto: %s",
                    getattr(transaction, "rollback_errors", ()),
                )
        except Exception as exc:
            success = False
            logger.error("Falha ao reverter transacao de codigo: %s", exc)
    return success


def restore_backups(
    restore_points: Iterable[Mapping[str, str]],
    resolve_path: Callable[[str], Path],
    logger: Any,
) -> bool:
    success = True
    for entry in reversed(tuple(restore_points)):
        original = resolve_path(entry["original"])
        backup = Path(entry["backup"])
        try:
            shutil.copy2(backup, original)
            backup.unlink(missing_ok=True)
        except OSError as exc:
            success = False
            logger.error("Falha ao restaurar '%s': %s", original, exc)
    return success


def remove_created_files(
    created_files: Iterable[str],
    resolve_path: Callable[[str], Path],
    logger: Any,
) -> bool:
    success = True
    for file_path in reversed(tuple(created_files)):
        target = resolve_path(file_path)
        try:
            target.unlink(missing_ok=True)
        except OSError as exc:
            success = False
            logger.error("Falha ao remover arquivo criado '%s': %s", target, exc)
    return success


__all__ = ["remove_created_files", "restore_backups", "rollback_transactions"]
