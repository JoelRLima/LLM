"""Offline archive-reset, backup inspection and restore service."""

from __future__ import annotations

import errno
import re
import secrets
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from agent.runtime.filesystem_primitives import FinalPathInspection, inspect_final_path
from agent.runtime.home_lifecycle import maintenance_guard
from agent.runtime.path_safety import WorkspacePathError, assert_no_link_ancestors
from agent.runtime.paths import AppPaths
from agent.runtime.storage_contracts import (
    LAYOUT_ID,
    LAYOUT_VERSION,
    MaintenanceConfirmation,
    MaintenanceOperation,
    StorageBackupDescriptor,
    StorageMaintenanceError,
    StorageRestoreResult,
    backup_id_is_valid,
    read_layout_marker,
    read_migration_receipt,
)

_BACKUP_TIMESTAMP = re.compile(r"\.backup\.(\d{8}T\d{6}\d{6}Z)\.[0-9a-f]{8}$")
_CANONICAL_TOP_LEVEL = {"config", "global", "workspaces", "cache", "logs"}


class StorageMaintenanceService:
    def __init__(self, paths: AppPaths) -> None:
        self.paths = paths

    def list_backups(self) -> tuple[StorageBackupDescriptor, ...]:
        parent = self.paths.home_dir.parent
        parent_inspection = self._inspect(parent)
        if not parent_inspection.exists:
            return ()
        if parent_inspection.is_link_like or parent_inspection.metadata is None or not stat.S_ISDIR(parent_inspection.metadata.st_mode):
            return ()
        descriptors: list[StorageBackupDescriptor] = []
        for entry in sorted(parent.iterdir(), key=lambda p: p.name):
            inspection = self._inspect(entry)
            if (
                not inspection.exists
                or inspection.is_link_like
                or inspection.metadata is None
                or not stat.S_ISDIR(inspection.metadata.st_mode)
                or not backup_id_is_valid(entry.name, self.paths.home_dir.name)
            ):
                continue
            try:
                descriptors.append(self._inspect_path(entry))
            except StorageMaintenanceError:
                continue
        return tuple(sorted(descriptors, key=lambda item: item.backup_id))

    def inspect_backup(self, backup_id: str) -> StorageBackupDescriptor:
        if not backup_id_is_valid(backup_id, self.paths.home_dir.name):
            raise StorageMaintenanceError("MAINTENANCE_BACKUP_NOT_FOUND")
        path = self.paths.home_dir.parent / backup_id
        inspection = self._inspect(path)
        if (
            not inspection.exists
            or inspection.is_link_like
            or inspection.metadata is None
            or not stat.S_ISDIR(inspection.metadata.st_mode)
        ):
            raise StorageMaintenanceError("MAINTENANCE_BACKUP_NOT_FOUND")
        return self._inspect_path(path)

    def reset(self, confirmation: MaintenanceConfirmation) -> StorageBackupDescriptor:
        self._validate_confirmation(confirmation, MaintenanceOperation.RESET)
        home = self.paths.home_dir
        home_inspection = self._inspect(home)
        if not home_inspection.exists:
            raise StorageMaintenanceError("MAINTENANCE_HOME_MISSING")
        self._require_directory(home, "MAINTENANCE_HOME_LIVENESS_INDETERMINATE")
        with maintenance_guard(home):
            self._validate_home(home, backup=False)
            backup_id = self._new_backup_id()
            backup = home.parent / backup_id
            if self._inspect(backup).exists:
                raise StorageMaintenanceError("MAINTENANCE_RENAME_FAILED")
            try:
                home.rename(backup)
            except OSError as exc:
                code = (
                    "MAINTENANCE_CROSS_FILESYSTEM"
                    if exc.errno == errno.EXDEV
                    else "MAINTENANCE_RENAME_FAILED"
                )
                raise StorageMaintenanceError(code, str(home)) from exc
        return self._inspect_path(backup)

    def restore(
        self,
        backup_id: str,
        confirmation: MaintenanceConfirmation,
    ) -> StorageRestoreResult:
        self._validate_confirmation(confirmation, MaintenanceOperation.RESTORE, backup_id=backup_id)
        home = self.paths.home_dir
        if self._inspect(home).exists:
            raise StorageMaintenanceError("MAINTENANCE_RESTORE_TARGET_EXISTS")
        with maintenance_guard(home):
            if self._inspect(home).exists:
                raise StorageMaintenanceError("MAINTENANCE_RESTORE_TARGET_EXISTS")
            if not backup_id_is_valid(backup_id, home.name):
                raise StorageMaintenanceError("MAINTENANCE_BACKUP_NOT_FOUND")
            backup = home.parent / backup_id
            self._require_directory(backup, "MAINTENANCE_BACKUP_NOT_FOUND")
            if self._inspect(backup).is_link_like:
                raise StorageMaintenanceError("MAINTENANCE_BACKUP_NOT_FOUND")
            descriptor = self._inspect_path(backup)
            try:
                descriptor.path.rename(home)
            except OSError as exc:
                code = (
                    "MAINTENANCE_CROSS_FILESYSTEM"
                    if exc.errno == errno.EXDEV
                    else "MAINTENANCE_RENAME_FAILED"
                )
                raise StorageMaintenanceError(code, str(descriptor.path)) from exc
        return StorageRestoreResult(backup_id=backup_id, restored_home=home)

    def _validate_confirmation(
        self,
        confirmation: MaintenanceConfirmation,
        operation: MaintenanceOperation,
        *,
        backup_id: str | None = None,
    ) -> None:
        if not isinstance(confirmation, MaintenanceConfirmation):
            raise StorageMaintenanceError("MAINTENANCE_CONFIRMATION_REQUIRED")
        if confirmation.operation is not operation:
            raise StorageMaintenanceError("MAINTENANCE_CONFIRMATION_MISMATCH")
        if Path(confirmation.canonical_home).expanduser().resolve() != self.paths.home_dir.resolve():
            raise StorageMaintenanceError("MAINTENANCE_CONFIRMATION_MISMATCH")
        if operation is MaintenanceOperation.RESTORE and confirmation.backup_id != backup_id:
            raise StorageMaintenanceError("MAINTENANCE_CONFIRMATION_MISMATCH")

    def _new_backup_id(self) -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        return f"{self.paths.home_dir.name}.backup.{timestamp}.{secrets.token_hex(4)}"

    @staticmethod
    def _inspect(path: Path) -> FinalPathInspection:
        try:
            return inspect_final_path(path)
        except OSError as exc:
            raise StorageMaintenanceError("MAINTENANCE_HOME_LIVENESS_INDETERMINATE", str(path)) from exc

    def _require_directory(self, path: Path, reason_code: str) -> None:
        inspection = self._inspect(path)
        if (
            not inspection.exists
            or inspection.is_link_like
            or inspection.metadata is None
            or not stat.S_ISDIR(inspection.metadata.st_mode)
        ):
            raise StorageMaintenanceError(reason_code, str(path))

    @staticmethod
    def _assert_no_link_ancestors(path: Path, reason_code: str) -> None:
        try:
            assert_no_link_ancestors(path)
        except (OSError, WorkspacePathError) as exc:
            raise StorageMaintenanceError(reason_code, str(path)) from exc

    def _inspect_path(self, path: Path) -> StorageBackupDescriptor:
        try:
            self._require_directory(path, "MAINTENANCE_BACKUP_INVALID")
            marker = self._validate_home(path, backup=True)
        except Exception as exc:
            if isinstance(exc, StorageMaintenanceError):
                raise
            raise StorageMaintenanceError("MAINTENANCE_BACKUP_INVALID", str(path)) from exc
        match = _BACKUP_TIMESTAMP.search(path.name)
        if match is None:
            raise StorageMaintenanceError("MAINTENANCE_BACKUP_INVALID")
        try:
            created = datetime.strptime(match.group(1), "%Y%m%dT%H%M%S%fZ").replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
        except ValueError as exc:
            raise StorageMaintenanceError("MAINTENANCE_BACKUP_INVALID") from exc
        return StorageBackupDescriptor(
            backup_id=path.name,
            path=path,
            created_at=created,
            layout_id=str(marker["layout_id"]),
            layout_version=cast(int, marker["layout_version"]),
        )

    def _validate_top_level(self, home: Path, allowed: set[str], code: str) -> set[str]:
        try:
            entries = tuple(home.iterdir())
            actual = {entry.name for entry in entries}
        except OSError as exc:
            raise StorageMaintenanceError(code, str(home)) from exc
        for entry in entries:
            inspection = self._inspect(entry)
            metadata = inspection.metadata
            canonical_entry = entry.name in _CANONICAL_TOP_LEVEL
            if (
                inspection.is_link_like
                or metadata is None
                or (canonical_entry and not stat.S_ISDIR(metadata.st_mode))
            ):
                raise StorageMaintenanceError(code, str(entry))
        return actual

    def _read_preserved_entries(self, receipt_path: Path, allowed: set[str], code: str) -> None:
        self._assert_no_link_ancestors(receipt_path, code)
        receipt_inspection = self._inspect(receipt_path)
        if receipt_inspection.is_link_like or (
            receipt_inspection.exists
            and (
                receipt_inspection.metadata is None
                or not stat.S_ISREG(receipt_inspection.metadata.st_mode)
            )
        ):
            raise StorageMaintenanceError(code, str(receipt_path))
        if receipt_inspection.exists:
            try:
                receipt = read_migration_receipt(receipt_path)
                preserved = receipt["preserved_legacy_top_level"]
                if not isinstance(preserved, list):
                    raise ValueError("invalid preserved list")
                allowed.update(str(item) for item in preserved)
            except Exception as exc:
                raise StorageMaintenanceError(code, str(receipt_path)) from exc

    def _validate_home(self, home: Path, *, backup: bool) -> dict[str, object]:
        code = "MAINTENANCE_BACKUP_INVALID" if backup else "MAINTENANCE_FOREIGN_ENTRY"
        self._require_directory(home, code)
        allowed = set(_CANONICAL_TOP_LEVEL)
        receipt_path = home / "global" / "migrations" / "w18_to_w19_v1.json"
        actual = self._validate_top_level(home, allowed, code)
        self._read_preserved_entries(receipt_path, allowed, code)
        if not actual.issubset(allowed):
            raise StorageMaintenanceError(code, str(sorted(actual - allowed)))
        marker_path = home / "global" / "storage_layout.json"
        self._assert_no_link_ancestors(marker_path, code)
        try:
            marker = read_layout_marker(marker_path)
        except Exception as exc:
            if isinstance(exc, StorageMaintenanceError):
                raise
            raise StorageMaintenanceError(code, str(marker_path)) from exc
        if marker["layout_id"] != LAYOUT_ID or marker["layout_version"] != LAYOUT_VERSION:
            raise StorageMaintenanceError(code)
        return marker


__all__ = ["StorageMaintenanceService"]
