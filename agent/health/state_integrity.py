"""Inspeção somente leitura do estado persistente usado no bootstrap."""

from __future__ import annotations

import sqlite3
import stat
from contextlib import closing
from pathlib import Path
from typing import Any

from agent.health.contracts import (
    STATUS_ERROR,
    STATUS_OK,
    STATUS_WARNING,
    CheckResult,
)
from agent.memory.json_persistence import JsonObjectReadError, read_json_object
from agent.memory.path_safety import (
    LinkLikePathError,
    inspect_final_path,
    reject_link_like,
)
from agent.runtime.paths import AppPaths
from agent.runtime.workspace_context import WorkspaceContext

LOG_SIZE_WARNING_BYTES = 10 * 1024 * 1024


def _state_files(
    app_paths: AppPaths,
    workspace: WorkspaceContext | None,
) -> tuple[dict[str, Path], Path | None, Path | None, Path | None]:
    files = {"agent.log": app_paths.log_file}
    if workspace is None:
        return files, None, None, None
    workspace_paths = app_paths.for_workspace(workspace.workspace_id)
    memory_file = workspace_paths.memory_file
    memory_database = workspace_paths.memory_db_file
    files.update(
        {
            "memory": memory_file,
            "memory_db": memory_database,
            "checkpoint": workspace_paths.checkpoint_file,
            "metrics": workspace_paths.metrics_file,
        }
    )
    return (
        files,
        memory_file,
        memory_database,
        workspace_paths.memory_backup_dir,
    )


def _inspect_regular_file(
    name: str,
    path: Path,
    warnings: list[str],
    problems: list[str],
) -> dict[str, Any]:
    item: dict[str, Any] = {"path": str(path)}
    try:
        inspection = inspect_final_path(path)
    except OSError as exc:
        item.update({"exists": None, "is_file": False, "error": str(exc)})
        problems.append(f"{name} não pôde ser inspecionado sem seguir links")
        return item

    item["exists"] = inspection.exists
    item["link_like"] = inspection.is_link_like
    if inspection.is_link_like:
        item.update({"is_file": False, "link_kind": inspection.link_kind})
        problems.append(f"{name} usa link e não será seguido")
        return item
    metadata = inspection.metadata
    is_file = metadata is not None and stat.S_ISREG(metadata.st_mode)
    item["is_file"] = is_file
    if inspection.exists and not is_file:
        problems.append(f"{name} não é arquivo regular")
        return item
    if not is_file or metadata is None:
        return item
    item["size_bytes"] = metadata.st_size
    if metadata.st_size > LOG_SIZE_WARNING_BYTES:
        warnings.append(f"{name} excede 10 MB")
    return item


def _inspect_json(path: Path, details: dict[str, Any], problems: list[str]) -> None:
    if not details["memory"].get("is_file"):
        return
    try:
        read_json_object(path)
        details["memory"]["integrity"] = "ok"
    except (JsonObjectReadError, OSError) as exc:
        details["memory"]["integrity"] = "error"
        details["memory"]["error"] = str(exc)
        problems.append("agent_memory.json inválido ou ilegível")


def _inspect_sqlite(
    path: Path,
    details: dict[str, Any],
    problems: list[str],
) -> None:
    if not details["memory_db"].get("is_file"):
        return
    try:
        reject_link_like(path)
        uri = f"file:{path.as_posix()}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            result = connection.execute("PRAGMA quick_check").fetchone()
        if not result or result[0] != "ok":
            raise sqlite3.DatabaseError(str(result))
        details["memory_db"]["integrity"] = "ok"
    except (LinkLikePathError, OSError, sqlite3.Error) as exc:
        details["memory_db"]["integrity"] = "error"
        details["memory_db"]["error"] = str(exc)
        problems.append("agent_memory.db falhou no quick_check somente leitura")


def _inspect_backup_directory(
    path: Path,
    details: dict[str, Any],
    problems: list[str],
) -> None:
    item: dict[str, Any] = {"path": str(path)}
    details["memory_backups"] = item
    try:
        inspection = inspect_final_path(path)
    except OSError as exc:
        item.update({"exists": None, "is_dir": False, "error": str(exc)})
        problems.append("memory_backups não pôde ser inspecionado sem seguir links")
        return
    item.update(
        {
            "exists": inspection.exists,
            "link_like": inspection.is_link_like,
            "is_dir": (
                inspection.metadata is not None
                and stat.S_ISDIR(inspection.metadata.st_mode)
                and not inspection.is_link_like
            ),
        }
    )
    if inspection.is_link_like:
        item["link_kind"] = inspection.link_kind
        problems.append("memory_backups usa link e não receberá cópias")
    elif inspection.exists and not item["is_dir"]:
        problems.append("memory_backups não é diretório regular")


def check_persistent_state(
    app_paths: AppPaths,
    workspace: WorkspaceContext | None,
) -> CheckResult:
    """Valida integridade sem criar, alterar ou bloquear os arquivos inspecionados."""

    files, memory_file, memory_database, backup_directory = _state_files(
        app_paths,
        workspace,
    )
    warnings: list[str] = []
    problems: list[str] = []
    details = {
        name: _inspect_regular_file(name, path, warnings, problems)
        for name, path in files.items()
    }
    if memory_file is not None:
        _inspect_json(memory_file, details, problems)
    if memory_database is not None:
        _inspect_sqlite(memory_database, details, problems)
    if backup_directory is not None:
        _inspect_backup_directory(backup_directory, details, problems)
    if problems:
        return CheckResult(
            "Estado e logs",
            STATUS_ERROR,
            (
                f"Estado persistente inválido: {'; '.join(problems)}. "
                "Restaure um backup ou migre o estado antes de iniciar."
            ),
            details,
        )
    return CheckResult(
        "Estado e logs",
        STATUS_WARNING if warnings else STATUS_OK,
        (
            "; ".join(warnings)
            if warnings
            else "Estado ausente ou com tamanho e integridade aceitáveis."
        ),
        details,
    )


__all__ = ["check_persistent_state"]
