"""Source discovery and source-preserving W18 -> W19 migration mechanics."""
from __future__ import annotations

import os
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Callable, cast

from agent.memory.json_persistence import write_text_atomic
from agent.runtime.filesystem_primitives import inspect_final_path
from agent.runtime.paths import AppPaths
from agent.runtime.storage_contracts import (
    StorageMigrationError,
    make_migration_receipt,
    read_layout_marker,
    read_migration_receipt,
    write_layout_marker,
    write_migration_receipt,
)
from agent.runtime.storage_migration_plan import (
    MigrationEntry,
    MigrationPlan,
    W18SourceProfile,
    _build_plan_for_profile,
    _hash,
    _safe_entry,
    _safe_source_path,
    build_migration_plan,
    has_durable_sources,
    source_profile_for,
)
from agent.runtime.storage_migration_revalidation import (
    preflight as _preflight,
)
from agent.runtime.storage_migration_revalidation import (
    revalidate_identical as _revalidate_identical_entries,
)
from agent.runtime.storage_migration_revalidation import (
    revalidate_sources as _revalidate_source_entries,
)
from agent.runtime.storage_migration_revalidation import (
    validate_destination_path as _validate_destination_path,
)


def _promote_entry(
    entry: MigrationEntry,
    home: Path,
    temporary: Path,
    record_created: Callable[[Path, os.stat_result], None] | None = None,
) -> os.stat_result:
    _validate_destination_path(entry.destination, home)
    if inspect_final_path(entry.destination).exists:
        _safe_entry(entry.destination, expected="file")
        raise StorageMigrationError("MIGRATION_TARGET_CONFLICT", str(entry.destination))
    expected = os.stat(temporary, follow_symlinks=False)
    publication_attempted = False
    try:
        publication_attempted = True
        os.replace(temporary, entry.destination)
        promoted = inspect_final_path(entry.destination)
        if (
            promoted.is_link_like
            or promoted.metadata is None
            or not stat.S_ISREG(promoted.metadata.st_mode)
            or not os.path.samestat(promoted.metadata, expected)
        ):
            raise StorageMigrationError("MIGRATION_PROMOTION_FAILED", str(entry.destination))
        if _hash(entry.destination) != _hash(entry.source):
            raise StorageMigrationError("MIGRATION_PROMOTION_FAILED", str(entry.destination))
        if record_created is not None:
            record_created(entry.destination, cast(os.stat_result, promoted.metadata))
        return cast(os.stat_result, promoted.metadata)
    except BaseException as exc:
        if publication_attempted:
            try:
                _remove_promoted_entry_if_present(entry.destination, expected)
            except StorageMigrationError as rollback_exc:
                raise rollback_exc from exc
        raise
def _copy_entry(
    entry: MigrationEntry,
    home: Path,
    record_created: Callable[[Path, os.stat_result], None] | None = None,
) -> os.stat_result:
    _validate_destination_path(entry.destination, home)
    entry.destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=entry.destination.parent, prefix=f".{entry.destination.name}.", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            with entry.source.open("rb") as source:
                shutil.copyfileobj(source, stream)
            stream.flush()
            os.fsync(stream.fileno())
        if _hash(temporary) != _hash(entry.source):
            raise StorageMigrationError("MIGRATION_PROMOTION_FAILED", str(entry.source))
        promoted_stat = _promote_entry(entry, home, temporary, record_created)
        temporary = None
        return promoted_stat
    except StorageMigrationError:
        raise
    except (OSError, shutil.Error) as exc:
        raise StorageMigrationError("MIGRATION_PROMOTION_FAILED", str(entry.source)) from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
def _remove_promoted_entry(path: Path, expected: os.stat_result) -> None:
    try:
        inspection = inspect_final_path(path)
        current = inspection.metadata
        if inspection.is_link_like or current is None or not stat.S_ISREG(current.st_mode) or not os.path.samestat(current, expected):
            raise StorageMigrationError("MIGRATION_ROLLBACK_UNPROVEN", str(path))
        path.unlink()
    except StorageMigrationError:
        raise
    except (FileNotFoundError, OSError) as exc:
        raise StorageMigrationError("MIGRATION_ROLLBACK_UNPROVEN", str(path)) from exc
def _remove_promoted_entry_if_present(path: Path, expected: os.stat_result) -> None:
    inspection = inspect_final_path(path)
    if not inspection.exists:
        return
    _remove_promoted_entry(path, expected)
def _restore_metadata_if_owned(
    path: Path, expected: os.stat_result, previous: bytes | None
) -> None:
    inspection = inspect_final_path(path)
    current = inspection.metadata
    if not inspection.exists:
        if previous is None:
            return
        raise StorageMigrationError("MIGRATION_ROLLBACK_UNPROVEN", str(path))
    if (
        previous is not None
        and not inspection.is_link_like
        and current is not None
        and stat.S_ISREG(current.st_mode)
        and path.read_bytes() == previous
    ):
        return
    if (
        inspection.is_link_like
        or current is None
        or not stat.S_ISREG(current.st_mode)
        or not os.path.samestat(current, expected)
    ):
        raise StorageMigrationError("MIGRATION_ROLLBACK_UNPROVEN", str(path))
    try:
        if previous is None:
            path.unlink()
            return
        try:
            content = previous.decode("utf-8")
            write_text_atomic(path, content)
        except BaseException:
            if path.read_bytes() == previous:
                return
            raise
    except BaseException as exc:
        raise StorageMigrationError("MIGRATION_ROLLBACK_UNPROVEN", str(path)) from exc
def _compatible_previous_receipt(plan: MigrationPlan, receipt_path: Path) -> bytes | None:
    if not receipt_path.exists():
        return None
    try:
        existing_receipt = read_migration_receipt(receipt_path)
        if existing_receipt["source_profile"] != plan.profile.name:
            raise StorageMigrationError("MIGRATION_TARGET_CONFLICT", str(receipt_path))
        return receipt_path.read_bytes()
    except Exception as exc:
        raise StorageMigrationError("MIGRATION_TARGET_CONFLICT", str(receipt_path)) from exc
def migrate_plan(plan: MigrationPlan, paths: AppPaths) -> Path:
    paths.ensure_base_directories()
    pending, identical, source_hashes = _preflight(plan, paths.home_dir)
    created: list[tuple[Path, os.stat_result]] = []
    receipt_path = paths.w18_to_w19_migration_receipt_file
    previous_receipt = _compatible_previous_receipt(plan, receipt_path)
    receipt_evidence: os.stat_result | None = None
    marker_evidence: os.stat_result | None = None
    def record_created(path: Path, evidence: os.stat_result) -> None:
        created.append((path, evidence))
    def record_receipt(evidence: os.stat_result) -> None:
        nonlocal receipt_evidence
        receipt_evidence = evidence
    def record_marker(evidence: os.stat_result) -> None:
        nonlocal marker_evidence
        marker_evidence = evidence
    try:
        for entry in pending:
            _copy_entry(entry, paths.home_dir, record_created)
        current_plan = _build_plan_for_profile(paths, plan.profile)
        planned_sources = {(entry.source, entry.destination, entry.relative) for entry in plan.entries}
        current_sources = {(entry.source, entry.destination, entry.relative) for entry in current_plan.entries}
        if planned_sources != current_sources:
            raise StorageMigrationError("MIGRATION_SOURCE_CHANGED")
        _revalidate_source_entries(source_hashes, _safe_source_path, _hash)
        _revalidate_identical_entries(
            plan, identical, source_hashes, paths.home_dir, _validate_destination_path, _hash
        )
        receipt = make_migration_receipt(
            source_profile=plan.profile.name,
            copied=[entry.relative for entry in pending],
            identical=identical,
            preserved_legacy_top_level=list(plan.preserved_legacy_top_level),
        )
        write_migration_receipt(receipt_path, receipt, publication_prepared=record_receipt)
        read_migration_receipt(receipt_path)
        write_layout_marker(paths.storage_layout_file, publication_prepared=record_marker)
        read_layout_marker(paths.storage_layout_file)
        return cast(Path, receipt_path)
    except StorageMigrationError:
        _rollback(created, receipt_path, previous_receipt, receipt_evidence, paths.storage_layout_file, marker_evidence)
        raise
    except BaseException as exc:
        _rollback(created, receipt_path, previous_receipt, receipt_evidence, paths.storage_layout_file, marker_evidence)
        raise StorageMigrationError("MIGRATION_PROMOTION_FAILED", str(exc)) from exc
def _rollback(
    created: list[tuple[Path, os.stat_result]],
    receipt_path: Path,
    previous_receipt: bytes | None,
    receipt_evidence: os.stat_result | None,
    marker_path: Path,
    marker_evidence: os.stat_result | None,
) -> None:
    failures: list[BaseException] = []
    for path, expected, previous in (
        (marker_path, marker_evidence, None),
        (receipt_path, receipt_evidence, previous_receipt),
    ):
        if expected is not None:
            try:
                _restore_metadata_if_owned(path, expected, previous)
            except BaseException as exc:
                failures.append(exc)
    for path, expected in reversed(created):
        try:
            _remove_promoted_entry_if_present(path, expected)
        except BaseException as exc:
            failures.append(exc)
    if failures:
        raise StorageMigrationError("MIGRATION_ROLLBACK_UNPROVEN", str(failures[0])) from failures[0]
__all__ = [
    "MigrationEntry",
    "MigrationPlan",
    "W18SourceProfile",
    "build_migration_plan",
    "has_durable_sources",
    "migrate_plan",
    "source_profile_for",
]
