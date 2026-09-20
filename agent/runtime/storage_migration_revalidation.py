"""Pre-commit identity and content revalidation for storage migration."""

from __future__ import annotations

import os
import stat
from collections.abc import Callable
from pathlib import Path

from agent.runtime.filesystem_primitives import inspect_final_path
from agent.runtime.storage_contracts import StorageMigrationError
from agent.runtime.storage_migration_plan import MigrationEntry, MigrationPlan
from agent.runtime.storage_migration_plan import _hash as hash_file
from agent.runtime.storage_migration_plan import _safe_entry as safe_entry
from agent.runtime.storage_migration_plan import _safe_source_path as safe_source_path


def validate_destination_path(destination: Path, home: Path) -> None:
    try:
        destination.resolve(strict=False).relative_to(home.resolve())
    except ValueError as exc:
        raise StorageMigrationError("MIGRATION_TARGET_UNSAFE", str(destination)) from exc
    current = destination.parent
    while True:
        if inspect_final_path(current).exists:
            safe_entry(current, expected="dir")
        if current == home:
            break
        if current.parent == current:
            raise StorageMigrationError("MIGRATION_TARGET_UNSAFE", str(destination))
        current = current.parent


def preflight(
    plan: MigrationPlan, home: Path
) -> tuple[list[MigrationEntry], list[str], dict[Path, tuple[str, os.stat_result]]]:
    pending: list[MigrationEntry] = []
    identical: list[str] = []
    source_hashes: dict[Path, tuple[str, os.stat_result]] = {}
    for entry in plan.entries:
        safe_source_path(entry.source, expected="file")
        source_inspection = inspect_final_path(entry.source)
        assert source_inspection.metadata is not None
        source_hashes[entry.source] = (hash_file(entry.source), source_inspection.metadata)
        validate_destination_path(entry.destination, home)
        if inspect_final_path(entry.destination).exists:
            safe_entry(entry.destination)
            if entry.destination.is_file() and hash_file(entry.destination) == source_hashes[entry.source][0]:
                identical.append(entry.relative)
            else:
                raise StorageMigrationError("MIGRATION_TARGET_CONFLICT", str(entry.destination))
        else:
            pending.append(entry)
    return pending, identical, source_hashes


def revalidate_sources(
    source_hashes: dict[Path, tuple[str, os.stat_result]],
    safe_source_path: Callable[..., bool],
    hash_file: Callable[[Path], str],
) -> None:
    for source, (expected_hash, expected_identity) in source_hashes.items():
        if not safe_source_path(source, expected="file"):
            raise StorageMigrationError("MIGRATION_SOURCE_CHANGED", str(source))
        current = inspect_final_path(source).metadata
        if current is None or not os.path.samestat(current, expected_identity) or hash_file(source) != expected_hash:
            raise StorageMigrationError("MIGRATION_SOURCE_CHANGED", str(source))


def revalidate_identical(
    plan: MigrationPlan,
    identical: list[str],
    source_hashes: dict[Path, tuple[str, os.stat_result]],
    home: Path,
    validate_destination: Callable[[Path, Path], None],
    hash_file: Callable[[Path], str],
) -> None:
    by_relative = {entry.relative: entry for entry in plan.entries}
    for relative in identical:
        entry = by_relative[relative]
        try:
            validate_destination(entry.destination, home)
            target = inspect_final_path(entry.destination)
            if not target.exists or target.is_link_like or target.metadata is None or not stat.S_ISREG(target.metadata.st_mode) or hash_file(entry.destination) != source_hashes[entry.source][0]:
                raise StorageMigrationError("MIGRATION_TARGET_CONFLICT", str(entry.destination))
        except StorageMigrationError as exc:
            if exc.reason_code == "MIGRATION_TARGET_CONFLICT":
                raise
            raise StorageMigrationError("MIGRATION_TARGET_CONFLICT", str(entry.destination)) from exc


__all__ = ["preflight", "revalidate_identical", "revalidate_sources", "validate_destination_path"]
