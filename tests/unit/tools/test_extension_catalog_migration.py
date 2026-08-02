import json
import subprocess
import sys
from pathlib import Path

import pytest

from agent.tools.extension_catalog_errors import (
    CatalogCorruptError,
    LegacyMigrationError,
)
from agent.tools.extension_catalog_lock import ExtensionCatalogLock
from agent.tools.extension_catalog_migration import migrate_legacy
from agent.tools.extension_catalog_service import ExtensionCatalogService
from agent.tools.extension_catalog_storage import ExtensionCatalogStorage

_MIGRATION_CHILD = """
import sys
from agent.tools.extension_catalog_errors import CatalogLockBusyError
from agent.tools.extension_catalog_migration import migrate_legacy

try:
    migrate_legacy(sys.argv[1], sys.argv[2])
except CatalogLockBusyError:
    print("BUSY")
"""


def _manifest(extension_id: str) -> dict[str, object]:
    return {
        "id": extension_id,
        "version": "1.0.0",
        "protocol_version": "1.0",
        "transport": "stdio",
        "entrypoint": ["python", "demo.py"],
        "timeout_seconds": 5,
        "tools": [{"name": "demo_tool", "schema": {}, "capabilities": ["read"]}],
    }


def _write_manifest(path: Path, extension_id: str) -> None:
    path.write_text(json.dumps(_manifest(extension_id)), encoding="utf-8")


def test_migration_is_explicit_all_or_nothing_and_idempotent(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    _write_manifest(first, "first.extension")
    _write_manifest(second, "second.extension")
    legacy = tmp_path / "registry.json"
    legacy_bytes = json.dumps(
        {
            "first.extension": {"manifest_path": str(first), "enabled": True},
            "second.extension": {"manifest_path": str(second), "enabled": False},
        },
        sort_keys=True,
    ).encode()
    legacy.write_bytes(legacy_bytes)
    destination = tmp_path / "extensions" / "catalog.json"

    migrated = migrate_legacy(legacy, destination)

    assert tuple(entry.extension_id for entry in migrated.entries) == (
        "first.extension",
        "second.extension",
    )
    assert legacy.read_bytes() == legacy_bytes
    assert migrate_legacy(legacy, destination) == migrated
    assert not (tmp_path / "workspace" / "extensions.json").exists()


def test_migration_accepts_enabled_absent_and_relative_with_explicit_base(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest, "demo.extension")
    legacy = tmp_path / "registry.json"
    legacy.write_text(
        json.dumps({"demo.extension": {"manifest_path": "manifest.json"}}),
        encoding="utf-8",
    )
    destination = tmp_path / "catalog.json"

    result = migrate_legacy(legacy, destination, base_dir=tmp_path)

    assert result.entries[0].manifest_path.persisted_value == manifest.as_posix()


@pytest.mark.parametrize(
    "legacy_value",
    [
        {"manifest_path": "manifest.json", "enabled": 1},
        {"manifest_path": "manifest.json", "unknown": True},
        {"enabled": True},
    ],
)
def test_migration_rejects_invalid_entry_without_destination(
    tmp_path: Path,
    legacy_value: dict[str, object],
) -> None:
    legacy = tmp_path / "registry.json"
    legacy.write_text(json.dumps({"demo.extension": legacy_value}), encoding="utf-8")
    destination = tmp_path / "catalog.json"

    with pytest.raises(LegacyMigrationError) as caught:
        migrate_legacy(legacy, destination, base_dir=tmp_path)

    assert caught.value.diagnostics
    assert not destination.exists()


def test_migration_collects_multiple_diagnostics_and_preserves_source(tmp_path: Path) -> None:
    legacy = tmp_path / "registry.json"
    original = b'{"bad.extension":{"manifest_path":"missing.json"},"other.extension":1}'
    legacy.write_bytes(original)
    destination = tmp_path / "catalog.json"

    with pytest.raises(LegacyMigrationError) as caught:
        migrate_legacy(legacy, destination, base_dir=tmp_path)

    assert len(caught.value.diagnostics) == 2
    assert legacy.read_bytes() == original
    assert not destination.exists()


def test_migration_rejects_duplicate_legacy_keys_without_touching_destination(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest, "demo.extension")
    legacy = tmp_path / "registry.json"
    legacy.write_bytes(
        (
            b'{"demo.extension":{"manifest_path":"'
            + manifest.as_posix().encode()
            + b'"},"demo.extension":{"manifest_path":"'
            + manifest.as_posix().encode()
            + b'"}}'
        )
    )
    destination = tmp_path / "catalog.json"

    with pytest.raises(LegacyMigrationError, match="duplicada"):
        migrate_legacy(legacy, destination)

    assert not destination.exists()


def test_migration_storage_failure_preserves_legacy_and_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest, "demo.extension")
    legacy = tmp_path / "registry.json"
    legacy_bytes = json.dumps({"demo.extension": {"manifest_path": str(manifest)}}).encode()
    legacy.write_bytes(legacy_bytes)
    destination = tmp_path / "catalog.json"

    def fail_save(self: object, document: object) -> None:
        raise OSError("storage failure")

    monkeypatch.setattr("agent.tools.extension_catalog_storage.ExtensionCatalogStorage.save", fail_save)

    with pytest.raises(OSError, match="storage failure"):
        migrate_legacy(legacy, destination)

    assert not destination.exists()
    assert legacy.read_bytes() == legacy_bytes


def test_migration_diagnostics_redact_manifest_content(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    payload = _manifest("demo.extension")
    payload["tools"][0]["description"] = {  # type: ignore[index]
        "SECRET-DESCRIPTION": "CONFIDENTIAL-VALUE"
    }
    payload["tools"][0]["name"] = "TOP-SECRET-NAME"  # type: ignore[index]
    payload["PRIVATE-FIELD"] = "CONFIDENTIAL-VALUE"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    legacy = tmp_path / "registry.json"
    legacy.write_text(
        json.dumps({"demo.extension": {"manifest_path": str(manifest)}}),
        encoding="utf-8",
    )

    with pytest.raises(LegacyMigrationError) as caught:
        migrate_legacy(legacy, tmp_path / "catalog.json")

    public = repr(caught.value.diagnostics)
    assert all(
        sentinel not in public
        for sentinel in (
            "TOP-SECRET-NAME",
            "SECRET-DESCRIPTION",
            "PRIVATE-FIELD",
            "CONFIDENTIAL-VALUE",
        )
    )


def test_migration_rejects_different_or_corrupt_existing_destination(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest, "demo.extension")
    legacy = tmp_path / "registry.json"
    legacy.write_text(
        json.dumps({"demo.extension": {"manifest_path": str(manifest)}}),
        encoding="utf-8",
    )
    destination = tmp_path / "catalog.json"
    destination.write_bytes(b"corrupt")

    with pytest.raises(CatalogCorruptError):
        migrate_legacy(legacy, destination)

    assert destination.read_bytes() == b"corrupt"


def test_migration_rejects_injected_storage_with_different_destination(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest, "demo.extension")
    legacy = tmp_path / "registry.json"
    legacy_bytes = json.dumps({"demo.extension": {"manifest_path": str(manifest)}}).encode()
    legacy.write_bytes(legacy_bytes)
    declared = tmp_path / "declared.json"
    actual = tmp_path / "actual.json"
    service = ExtensionCatalogService(ExtensionCatalogStorage(actual))

    with pytest.raises(LegacyMigrationError, match="Storage injetado"):
        migrate_legacy(legacy, declared, service=service)

    assert not declared.exists()
    assert not actual.exists()
    assert legacy.read_bytes() == legacy_bytes


def test_migration_rejects_injected_lock_with_different_destination(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest, "demo.extension")
    legacy = tmp_path / "registry.json"
    legacy.write_text(
        json.dumps({"demo.extension": {"manifest_path": str(manifest)}}),
        encoding="utf-8",
    )
    destination = tmp_path / "catalog.json"
    service = ExtensionCatalogService(
        ExtensionCatalogStorage(destination),
        lock=ExtensionCatalogLock(tmp_path / "other.lock"),
    )

    with pytest.raises(LegacyMigrationError, match="Lock injetado"):
        migrate_legacy(legacy, destination, service=service)

    assert not destination.exists()


def test_migration_does_not_overwrite_while_writer_holds_lock(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest, "demo.extension")
    legacy = tmp_path / "registry.json"
    legacy_bytes = json.dumps(
        {"demo.extension": {"manifest_path": str(manifest)}},
        sort_keys=True,
    ).encode()
    legacy.write_bytes(legacy_bytes)
    destination = tmp_path / "catalog.json"
    service = ExtensionCatalogService(ExtensionCatalogStorage(destination))
    service.lock.acquire()
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            _MIGRATION_CHILD,
            str(legacy),
            str(destination),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    service.lock.release()

    assert child.stdout.strip() == "BUSY"
    assert not destination.exists()
    assert list(tmp_path.glob(".catalog.json.*.tmp")) == []
    assert legacy.read_bytes() == legacy_bytes
