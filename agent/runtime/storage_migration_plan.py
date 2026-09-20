"""W18 source discovery and canonical W19 migration-plan construction."""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from agent.runtime.filesystem_primitives import inspect_final_path
from agent.runtime.paths import AppPaths
from agent.runtime.storage_contracts import StorageLayoutError, StorageMigrationError
from agent.runtime.storage_migration_sources import W18SourceProfile, source_profile_for

_WORKSPACE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_DATA_RECURSIVE = ("memory_backups", "task_definitions")
_STATE_RECURSIVE = ("reports", "artifacts", "restore_points", "traces", "trace_exports")
_DATA_FILES = ("agent_memory.json", "agent_memory.db", "chat_history.json", "extensions.json")
_STATE_FILES = (
    "agent_checkpoint.json",
    "agent_metrics.jsonl",
    "task_tracker.json",
    "task_tracker.md",
    "benchmark_results.json",
)


@dataclass(frozen=True, slots=True)
class MigrationEntry:
    source: Path
    destination: Path
    relative: str


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    profile: W18SourceProfile
    entries: tuple[MigrationEntry, ...]
    preserved_legacy_top_level: tuple[str, ...]


def _safe_entry(path: Path, *, expected: str | None = None) -> bool:
    try:
        inspection = inspect_final_path(path)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise StorageMigrationError("MIGRATION_SOURCE_UNSAFE", str(path)) from exc
    if not inspection.exists:
        return False
    if inspection.is_link_like or inspection.metadata is None:
        raise StorageMigrationError("MIGRATION_SOURCE_UNSAFE", str(path))
    info = inspection.metadata
    if expected == "file" and not stat.S_ISREG(info.st_mode):
        raise StorageMigrationError("MIGRATION_SOURCE_INVALID", str(path))
    if expected == "dir" and not stat.S_ISDIR(info.st_mode):
        raise StorageMigrationError("MIGRATION_SOURCE_INVALID", str(path))
    return True


def _safe_source_path(path: Path, *, expected: str | None = None) -> bool:
    """Validate the final source entry and every existing ancestor."""

    current = path
    chain: list[Path] = []
    while True:
        chain.append(current)
        if current == current.parent:
            break
        current = current.parent
    for candidate in reversed(chain):
        if candidate == path:
            continue
        if inspect_final_path(candidate).exists:
            _safe_entry(candidate)
    return _safe_entry(path, expected=expected)


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise StorageMigrationError("MIGRATION_SOURCE_INVALID", str(path)) from exc
    return digest.hexdigest()


def _validate_durable_file(path: Path) -> None:
    _safe_source_path(path, expected="file")
    suffix = path.suffix.casefold()
    try:
        if suffix in {".json"}:
            from agent.runtime.storage_contracts import load_strict_json

            load_strict_json(path)
        elif suffix == ".jsonl":
            raw = path.read_text(encoding="utf-8")
            for line in raw.splitlines():
                if line.strip():
                    import json

                    json.loads(line)
        elif path.name == "agent_memory.db":
            connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
            try:
                result = connection.execute("PRAGMA quick_check").fetchone()
                if not result or str(result[0]).casefold() != "ok":
                    raise StorageMigrationError("MIGRATION_SOURCE_INVALID", str(path))
            finally:
                connection.close()
        else:
            _hash(path)
    except StorageLayoutError as exc:
        raise StorageMigrationError(exc.reason_code, str(path)) from exc
    except StorageMigrationError:
        raise
    except (OSError, UnicodeError, ValueError, sqlite3.Error) as exc:
        raise StorageMigrationError("MIGRATION_SOURCE_INVALID", str(path)) from exc


def _iter_tree(root: Path) -> Iterable[Path]:
    if not _safe_entry(root, expected="dir"):
        return ()
    result: list[Path] = []
    pending = [root]
    while pending:
        current = pending.pop()
        try:
            entries = sorted(os.scandir(current), key=lambda entry: entry.name)
        except OSError as exc:
            raise StorageMigrationError("MIGRATION_SOURCE_UNSAFE", str(current)) from exc
        directories: list[Path] = []
        for entry in entries:
            child = Path(entry.path)
            inspection = inspect_final_path(child)
            if not inspection.exists:
                continue
            if inspection.is_link_like or inspection.metadata is None:
                raise StorageMigrationError("MIGRATION_SOURCE_UNSAFE", str(child))
            if stat.S_ISDIR(inspection.metadata.st_mode):
                directories.append(child)
            elif stat.S_ISREG(inspection.metadata.st_mode):
                result.append(child)
            else:
                raise StorageMigrationError("MIGRATION_SOURCE_INVALID", str(child))
        pending.extend(reversed(directories))
    return tuple(result)


def _append_file(entries: list[MigrationEntry], source: Path, destination: Path, home: Path) -> None:
    if not _safe_source_path(source, expected="file"):
        return
    _validate_durable_file(source)
    try:
        relative = destination.resolve().relative_to(home.resolve()).as_posix()
    except ValueError as exc:
        raise StorageMigrationError("MIGRATION_TARGET_UNSAFE", str(destination)) from exc
    entries.append(MigrationEntry(source, destination, relative))


def _append_workspace_tree(
    entries: list[MigrationEntry],
    source_root: Path,
    destination_root: Path,
    home: Path,
    allowed_files: tuple[str, ...],
    recursive: tuple[str, ...],
) -> None:
    if not _safe_source_path(source_root, expected="dir"):
        return
    _safe_source_path(source_root, expected="dir")
    for child in sorted(source_root.iterdir(), key=lambda p: p.name):
        _safe_entry(child)
        if child.name in allowed_files:
            _append_file(entries, child, destination_root / child.name, home)
        elif child.name in recursive:
            _safe_entry(child, expected="dir")
            for source_file in _iter_tree(child):
                relative = source_file.relative_to(source_root)
                _append_file(entries, source_file, destination_root / relative, home)


def _append_fixed_entries(entries: list[MigrationEntry], paths: AppPaths, profile: W18SourceProfile, home: Path) -> None:
    _append_file(entries, profile.config_dir / "config.json", paths.config_file, home)
    for filename, destination in (
        ("registry.json", paths.extensions_registry_file),
        ("catalog.json", paths.extensions_catalog_file),
    ):
        _append_file(entries, profile.data_dir / "extensions" / filename, destination, home)
    for filename, destination in (
        ("health_report.json", paths.health_report_file),
        ("last_workspace.json", paths.last_workspace_file),
    ):
        _append_file(entries, profile.state_dir / filename, destination, home)


def _workspace_ids(profile: W18SourceProfile) -> set[str]:
    workspace_ids: set[str] = set()
    for root in (profile.data_dir / "workspaces", profile.state_dir / "workspaces"):
        if not _safe_source_path(root, expected="dir"):
            continue
        _safe_source_path(root, expected="dir")
        for child in sorted(root.iterdir(), key=lambda p: p.name):
            _safe_entry(child, expected="dir")
            if not _WORKSPACE_ID.fullmatch(child.name):
                raise StorageMigrationError("MIGRATION_SOURCE_INVALID", child.name)
            workspace_ids.add(child.name)
    return workspace_ids


def _append_workspace_entries(
    entries: list[MigrationEntry],
    paths: AppPaths,
    profile: W18SourceProfile,
    home: Path,
    workspace_ids: set[str],
) -> None:
    source_data = profile.data_dir / "workspaces"
    source_state = profile.state_dir / "workspaces"
    for workspace_id in sorted(workspace_ids):
        target = paths.for_workspace(workspace_id)
        _append_workspace_tree(entries, source_data / workspace_id, target.data_dir, home, _DATA_FILES, _DATA_RECURSIVE)
        _append_workspace_tree(entries, source_state / workspace_id, target.state_dir, home, _STATE_FILES, _STATE_RECURSIVE)


def _preserved_top_level(home: Path, profile: W18SourceProfile) -> tuple[str, ...]:
    preserved = [name for name in profile.preserved_candidates if _safe_source_path(home / name)]
    return tuple(sorted(set(preserved)))


def _build_plan_for_profile(paths: AppPaths, profile: W18SourceProfile) -> MigrationPlan:
    entries: list[MigrationEntry] = []
    home = paths.home_dir
    _append_fixed_entries(entries, paths, profile, home)
    _append_workspace_entries(entries, paths, profile, home, _workspace_ids(profile))
    entries.sort(key=lambda item: item.relative)
    return MigrationPlan(profile, tuple(entries), _preserved_top_level(home, profile))


def build_migration_plan(paths: AppPaths, environment: Mapping[str, str] | None = None) -> MigrationPlan:
    return _build_plan_for_profile(paths, source_profile_for(paths, environment))


def has_durable_sources(plan: MigrationPlan) -> bool:
    return bool(plan.entries)


__all__ = [
    "MigrationEntry",
    "MigrationPlan",
    "W18SourceProfile",
    "build_migration_plan",
    "has_durable_sources",
    "source_profile_for",
]
