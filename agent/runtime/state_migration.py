"""Explicit, non-destructive migration of legacy runtime state."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from agent.runtime.instance_lock import InstanceLock, InstanceLockError
from agent.runtime.paths import WorkspacePaths


class StateMigrationError(RuntimeError):
    """Raised before promotion when legacy state is unsafe or conflicting."""


@dataclass(frozen=True)
class StateMigrationReport:
    source: str
    copied: tuple[str, ...]
    skipped: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "copied": list(self.copied),
            "skipped": list(self.skipped),
        }


def _digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _validate_sqlite(path: Path) -> None:
    try:
        with closing(
            sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        ) as connection:
            result = connection.execute("PRAGMA quick_check").fetchone()
    except sqlite3.Error as exc:
        raise StateMigrationError(f"SQLite inválido: {path}") from exc
    if not result or result[0] != "ok":
        raise StateMigrationError(f"SQLite não passou no quick_check: {path}")


def _validate_json(path: Path) -> None:
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StateMigrationError(f"JSON inválido: {path}") from exc


def _validate_json_lines(path: Path) -> None:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                json.loads(line)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StateMigrationError(f"JSONL inválido: {path}") from exc


def _validate_file(path: Path) -> None:
    if path.is_symlink():
        raise StateMigrationError(f"Links simbólicos não são aceitos: {path}")
    if path.name == "agent_memory.db":
        _validate_sqlite(path)
    elif path.suffix == ".json":
        _validate_json(path)
    elif path.suffix == ".jsonl":
        _validate_json_lines(path)


def _files_under(source: Path) -> tuple[Path, ...]:
    if source.is_symlink():
        raise StateMigrationError(f"Links simbólicos não são aceitos: {source}")
    if source.is_file():
        return (source,)
    if not source.is_dir():
        return ()
    files = tuple(sorted(item for item in source.rglob("*") if item.is_file()))
    if any(item.is_symlink() for item in source.rglob("*")):
        raise StateMigrationError(f"Links simbólicos não são aceitos: {source}")
    return files


def _mapping(paths: WorkspacePaths) -> tuple[tuple[str, Path], ...]:
    return (
        ("agent_memory.json", paths.memory_file),
        ("agent_memory.db", paths.memory_db_file),
        ("memory_backups", paths.memory_backup_dir),
        ("agent_checkpoint.json", paths.checkpoint_file),
        ("agent_metrics.jsonl", paths.metrics_file),
        ("reports", paths.reports_dir),
        ("restore_points", paths.restore_points_dir),
        ("chat_history.json", paths.chat_history_file),
        ("task_tracker.json", paths.task_tracker_json),
        ("task_tracker.md", paths.task_tracker_markdown),
        ("benchmark_results.json", paths.benchmark_results_file),
    )


def _pairs(source_root: Path, paths: WorkspacePaths) -> tuple[tuple[Path, Path], ...]:
    pairs: list[tuple[Path, Path]] = []
    for relative, destination in _mapping(paths):
        source = source_root / relative
        for item in _files_under(source):
            target = destination if source.is_file() else destination / item.relative_to(source)
            pairs.append((item, target))
    return tuple(pairs)


def _preflight(
    pairs: tuple[tuple[Path, Path], ...],
) -> tuple[list[tuple[Path, Path]], list[str], list[str]]:
    pending: list[tuple[Path, Path]] = []
    copied: list[str] = []
    skipped: list[str] = []
    for source, destination in pairs:
        _validate_file(source)
        label = source.name
        if not destination.exists():
            pending.append((source, destination))
            copied.append(label)
            continue
        if (
            destination.is_symlink()
            or not destination.is_file()
            or _digest(source) != _digest(destination)
        ):
            raise StateMigrationError(f"Destino conflitante: {destination}")
        skipped.append(label)
    return pending, copied, skipped


def _copy_atomic(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _promote_all(pending: list[tuple[Path, Path]]) -> None:
    promoted: list[Path] = []
    try:
        for source, target in pending:
            promoted.append(target)
            _copy_atomic(source, target)
    except Exception as exc:
        for target in reversed(promoted):
            target.unlink(missing_ok=True)
        raise StateMigrationError(
            "A migração falhou; arquivos promovidos foram revertidos."
        ) from exc


def migrate_legacy_state(
    legacy_runtime: str | Path,
    destination: WorkspacePaths,
) -> StateMigrationReport:
    """Copy allowlisted state into one workspace and preserve the source."""

    source_root = Path(legacy_runtime).expanduser().resolve()
    if not source_root.is_dir():
        raise StateMigrationError(f"Runtime legado não encontrado: {source_root}")
    lock = InstanceLock.create(destination.lock_file)
    try:
        lock.acquire()
    except InstanceLockError as exc:
        raise StateMigrationError(
            f"O estado do workspace está em uso: {destination.lock_file}"
        ) from exc
    try:
        pairs = _pairs(source_root, destination)
        pending, copied, skipped = _preflight(pairs)
        _promote_all(pending)
        return StateMigrationReport(
            source=str(source_root),
            copied=tuple(copied),
            skipped=tuple(skipped),
        )
    finally:
        lock.release()


__all__ = [
    "StateMigrationError",
    "StateMigrationReport",
    "migrate_legacy_state",
]
