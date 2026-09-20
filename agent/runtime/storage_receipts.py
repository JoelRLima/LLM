"""Canonical W18 -> W19 migration receipt contract and persistence."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, NoReturn

from agent.memory.json_persistence import write_text_atomic
from agent.runtime.storage_contracts import (
    MIGRATION_ID,
    MIGRATION_SCHEMA_VERSION,
    StorageLayoutError,
    StorageMigrationError,
    load_strict_json,
)

_SOURCE_PROFILES = {"explicit_home", "legacy_runtime", "windows_default", "xdg_default"}
_NOT_MIGRATED = ["cache", "logs", "lock_files"]
_PRESERVED_BY_PROFILE = {
    "explicit_home": {"data", "state"},
    "legacy_runtime": {
        "data",
        "health_report.json",
        "last_workspace.json",
        "agent_memory.json",
        "agent_memory.db",
        "memory_backups",
        "agent_checkpoint.json",
        "agent_metrics.jsonl",
        "reports",
        "restore_points",
        "chat_history.json",
        "task_tracker.json",
        "task_tracker.md",
        "benchmark_results.json",
    },
    "windows_default": set(),
    "xdg_default": set(),
}
_RECEIPT_KEYS = {
    "schema_version",
    "migration_id",
    "completed_at",
    "source_profile",
    "copied",
    "identical",
    "not_migrated",
    "preserved_legacy_top_level",
    "source_preserved",
}
_SORTED_LIST_KEYS = {"copied", "identical", "preserved_legacy_top_level"}
_PATH_LIST_KEYS = {"copied", "identical"}


def _invalid(message: str) -> NoReturn:
    raise StorageMigrationError("MIGRATION_TARGET_CONFLICT", message)


def _validate_receipt_header(value: Mapping[str, Any]) -> str:
    if set(value) != _RECEIPT_KEYS:
        _invalid("invalid migration receipt keys")
    if value["schema_version"] != MIGRATION_SCHEMA_VERSION or value["migration_id"] != MIGRATION_ID:
        _invalid("invalid migration receipt version")
    source_profile = value["source_profile"]
    if source_profile not in _SOURCE_PROFILES:
        _invalid("invalid source profile")
    if value["source_preserved"] is not True:
        _invalid("source_preserved must be true")
    try:
        completed_at = str(value["completed_at"])
        parsed = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
        if not completed_at.endswith("Z") or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
            raise ValueError("timestamp is not UTC")
    except (TypeError, ValueError) as exc:
        raise StorageMigrationError("MIGRATION_TARGET_CONFLICT", "invalid completed_at") from exc
    return str(source_profile)


def _receipt_list(value: object, key: str) -> list[str]:
    if not isinstance(value, list):
        _invalid(f"invalid {key}")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str):
            _invalid(f"invalid {key}")
        items.append(item)
    return items


def _validate_sorted_list(items: list[str], key: str) -> None:
    if items != sorted(set(items)):
        _invalid(f"unsorted {key}")


def _validate_safe_paths(items: list[str], key: str) -> None:
    if any(
        not item or item.startswith("/") or "\\" in item or any(part == ".." for part in item.split("/"))
        for item in items
    ):
        _invalid(f"unsafe {key}")


def _validate_receipt_lists(value: Mapping[str, Any]) -> dict[str, list[str]]:
    lists: dict[str, list[str]] = {}
    for key in ("copied", "identical", "not_migrated", "preserved_legacy_top_level"):
        items = _receipt_list(value[key], key)
        if key in _SORTED_LIST_KEYS:
            _validate_sorted_list(items, key)
        if key in _PATH_LIST_KEYS:
            _validate_safe_paths(items, key)
        lists[key] = items
    return lists


def _validate_preserved_entries(source_profile: str, preserved: list[str]) -> None:
    if not set(preserved).issubset(_PRESERVED_BY_PROFILE[source_profile]):
        _invalid("invalid preserved entry")


def validate_migration_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    source_profile = _validate_receipt_header(value)
    lists = _validate_receipt_lists(value)
    if lists["not_migrated"] != _NOT_MIGRATED:
        _invalid("invalid not_migrated")
    _validate_preserved_entries(source_profile, lists["preserved_legacy_top_level"])
    return {
        "schema_version": MIGRATION_SCHEMA_VERSION,
        "migration_id": MIGRATION_ID,
        "completed_at": str(value["completed_at"]),
        "source_profile": source_profile,
        **lists,
        "source_preserved": True,
    }


def read_migration_receipt(path: Path) -> dict[str, Any]:
    try:
        return validate_migration_receipt(load_strict_json(path, max_bytes=2 * 1024 * 1024))
    except StorageMigrationError:
        raise
    except StorageLayoutError as exc:
        raise StorageMigrationError("MIGRATION_TARGET_CONFLICT", str(path)) from exc


def make_migration_receipt(
    *,
    source_profile: str,
    copied: list[str],
    identical: list[str],
    preserved_legacy_top_level: list[str],
) -> dict[str, Any]:
    receipt = {
        "schema_version": MIGRATION_SCHEMA_VERSION,
        "migration_id": MIGRATION_ID,
        "completed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_profile": source_profile,
        "copied": sorted(set(copied)),
        "identical": sorted(set(identical)),
        "not_migrated": list(_NOT_MIGRATED),
        "preserved_legacy_top_level": sorted(set(preserved_legacy_top_level)),
        "source_preserved": True,
    }
    return validate_migration_receipt(receipt)


def write_migration_receipt(
    path: Path,
    receipt: Mapping[str, Any],
    *,
    publication_prepared: Callable[[os.stat_result], None] | None = None,
) -> dict[str, Any]:
    normalized = validate_migration_receipt(receipt)
    try:
        write_text_atomic(
            path,
            json.dumps(normalized, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            publication_prepared=publication_prepared,
        )
        return read_migration_receipt(path)
    except StorageMigrationError:
        raise
    except OSError as exc:
        raise StorageMigrationError("MIGRATION_PROMOTION_FAILED", str(path)) from exc


__all__ = [
    "make_migration_receipt",
    "read_migration_receipt",
    "validate_migration_receipt",
    "write_migration_receipt",
]
