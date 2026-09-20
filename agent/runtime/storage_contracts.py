"""Typed contracts for canonical W19 storage lifecycle operations."""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, cast

from agent.memory.json_persistence import write_text_atomic
from agent.runtime.filesystem_primitives import inspect_final_path
from agent.runtime.path_safety import WorkspacePathError, assert_no_link_ancestors

LAYOUT_SCHEMA_VERSION = 1
LAYOUT_ID = "w19-single-root-v1"
LAYOUT_VERSION = 19
MIGRATION_SCHEMA_VERSION = 1
MIGRATION_ID = "w18-to-w19-single-root-v1"


class StorageLayoutStatus(str, Enum):
    FRESH = "fresh"
    CANONICAL = "canonical"
    MIGRATION_REQUIRED = "migration_required"


class StorageError(RuntimeError):
    """Base error with a stable machine-readable reason code."""

    def __init__(self, reason_code: str, message: str | None = None) -> None:
        self.reason_code = reason_code
        super().__init__(message or reason_code)


class StorageLayoutError(StorageError):
    pass


class StorageMigrationError(StorageError):
    pass


class StorageMaintenanceError(StorageError):
    pass


@dataclass(frozen=True, slots=True)
class StorageBootstrapResult:
    initial_status: StorageLayoutStatus
    final_status: StorageLayoutStatus
    migrated: bool
    receipt_path: Path | None


@dataclass(frozen=True, slots=True)
class MaintenanceConfirmation:
    operation: "MaintenanceOperation"
    canonical_home: str
    backup_id: str | None = None


class MaintenanceOperation(str, Enum):
    RESET = "reset"
    RESTORE = "restore"


@dataclass(frozen=True, slots=True)
class StorageBackupDescriptor:
    backup_id: str
    path: Path
    created_at: str
    layout_id: str
    layout_version: int


@dataclass(frozen=True, slots=True)
class StorageRestoreResult:
    backup_id: str
    restored_home: Path


_LEASE_KEYS = frozenset({"schema_version", "pid", "process_start_id", "token", "home_fingerprint", "created_at"})


def validate_home_lease(value: object, *, path: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _LEASE_KEYS or value.get("schema_version") != 1 or isinstance(value.get("schema_version"), bool):
        raise StorageMaintenanceError("MAINTENANCE_HOME_LIVENESS_INDETERMINATE", path)
    pid, start_id = value.get("pid"), value.get("process_start_id")
    fingerprint, token, created_at = value.get("home_fingerprint"), value.get("token"), value.get("created_at")
    valid_timestamp = isinstance(created_at, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", created_at) is not None
    if valid_timestamp:
        try:
            parsed = datetime.fromisoformat(cast(str, created_at)[:-1] + "+00:00")
            valid_timestamp = parsed.utcoffset() == timezone.utc.utcoffset(parsed)
        except ValueError:
            valid_timestamp = False
    if (
        not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0
        or (start_id is not None and (not isinstance(start_id, str) or not start_id))
        or not isinstance(fingerprint, str) or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None
        or not isinstance(token, str) or re.fullmatch(r"[0-9a-f]{32}", token) is None or not valid_timestamp
    ):
        raise StorageMaintenanceError("MAINTENANCE_HOME_LIVENESS_INDETERMINATE", path)
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StorageLayoutError("MIGRATION_LAYOUT_MARKER_INVALID", "JSON duplicate key")
        result[key] = value
    return result


def load_strict_json(path: Path, *, max_bytes: int = 8 * 1024 * 1024) -> dict[str, Any]:
    """Load one bounded, link-free JSON object."""

    try:
        assert_no_link_ancestors(path)
        inspection = inspect_final_path(path)
        if (
            not inspection.exists
            or inspection.is_link_like
            or inspection.metadata is None
            or not stat.S_ISREG(inspection.metadata.st_mode)
        ):
            raise StorageLayoutError("MIGRATION_SOURCE_UNSAFE", str(path))
        raw = path.read_bytes()
        if len(raw) > max_bytes:
            raise StorageLayoutError("MIGRATION_SOURCE_INVALID", str(path))
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except StorageError:
        raise
    except WorkspacePathError as exc:
        raise StorageLayoutError("MIGRATION_SOURCE_UNSAFE", str(path)) from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StorageLayoutError("MIGRATION_SOURCE_INVALID", str(path)) from exc
    if not isinstance(value, dict):
        raise StorageLayoutError("MIGRATION_SOURCE_INVALID", str(path))
    return value


def validate_layout_marker(value: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "schema_version": LAYOUT_SCHEMA_VERSION,
        "layout_id": LAYOUT_ID,
        "layout_version": LAYOUT_VERSION,
    }
    if dict(value) != expected:
        raise StorageLayoutError("MIGRATION_LAYOUT_MARKER_INVALID")
    return dict(expected)


def read_layout_marker(path: Path) -> dict[str, Any]:
    try:
        return validate_layout_marker(load_strict_json(path, max_bytes=4096))
    except StorageLayoutError as exc:
        if exc.reason_code == "MIGRATION_SOURCE_UNSAFE":
            raise StorageLayoutError("MIGRATION_LAYOUT_MARKER_INVALID", str(path)) from exc
        raise


def write_layout_marker(
    path: Path,
    *,
    publication_prepared: Callable[[os.stat_result], None] | None = None,
) -> dict[str, Any]:
    marker = validate_layout_marker(
        {
            "schema_version": LAYOUT_SCHEMA_VERSION,
            "layout_id": LAYOUT_ID,
            "layout_version": LAYOUT_VERSION,
        }
    )
    try:
        write_text_atomic(
            path,
            json.dumps(marker, sort_keys=True, separators=(",", ":")) + "\n",
            publication_prepared=publication_prepared,
        )
        return read_layout_marker(path)
    except StorageError:
        raise
    except OSError as exc:
        raise StorageLayoutError("MIGRATION_PROMOTION_FAILED", str(path)) from exc


def validate_migration_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    from agent.runtime.storage_receipts import validate_migration_receipt as validate

    return validate(value)


def read_migration_receipt(path: Path) -> dict[str, Any]:
    from agent.runtime.storage_receipts import read_migration_receipt as read

    return read(path)


def make_migration_receipt(
    *,
    source_profile: str,
    copied: list[str],
    identical: list[str],
    preserved_legacy_top_level: list[str],
) -> dict[str, Any]:
    from agent.runtime.storage_receipts import make_migration_receipt as make

    return make(
        source_profile=source_profile,
        copied=copied,
        identical=identical,
        preserved_legacy_top_level=preserved_legacy_top_level,
    )


def write_migration_receipt(
    path: Path,
    receipt: Mapping[str, Any],
    *,
    publication_prepared: Callable[[os.stat_result], None] | None = None,
) -> dict[str, Any]:
    from agent.runtime.storage_receipts import write_migration_receipt as write

    return write(path, receipt, publication_prepared=publication_prepared)


def backup_id_is_valid(backup_id: str, home_name: str) -> bool:
    pattern = rf"^{re.escape(home_name)}\.backup\.\d{{8}}T\d{{6}}\d{{6}}Z\.[0-9a-f]{{8}}$"
    return bool(re.fullmatch(pattern, backup_id))


__all__ = [
    "LAYOUT_ID",
    "LAYOUT_VERSION",
    "MaintenanceConfirmation",
    "MaintenanceOperation",
    "StorageBackupDescriptor",
    "StorageBootstrapResult",
    "StorageError",
    "StorageLayoutError",
    "StorageLayoutStatus",
    "StorageMaintenanceError",
    "StorageMigrationError",
    "StorageRestoreResult",
    "backup_id_is_valid",
    "load_strict_json",
    "make_migration_receipt",
    "read_layout_marker",
    "read_migration_receipt",
    "validate_layout_marker",
    "validate_home_lease",
    "validate_migration_receipt",
    "write_layout_marker",
    "write_migration_receipt",
]
