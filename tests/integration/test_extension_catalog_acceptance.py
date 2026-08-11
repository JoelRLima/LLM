import json
from pathlib import Path

import pytest

from agent.tools.extension_catalog_errors import CatalogDriftError
from agent.tools.extension_catalog_migration import migrate_legacy
from agent.tools.extension_catalog_service import ExtensionCatalogService
from agent.tools.extension_catalog_storage import ExtensionCatalogStorage


def _manifest(extension_id: str = "demo.extension", version: str = "1.0.0") -> dict[str, object]:
    return {
        "id": extension_id,
        "version": version,
        "protocol_version": "1.0",
        "transport": "stdio",
        "entrypoint": ["python", "demo.py"],
        "timeout_seconds": 5,
        "tools": [{"name": "demo_tool", "schema": {}, "capabilities": ["read", "process"]}],
    }


def test_catalog_lifecycle_acceptance(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(_manifest()), encoding="utf-8")
    catalog_path = tmp_path / "extensions" / "catalog.json"
    service = ExtensionCatalogService(ExtensionCatalogStorage(catalog_path))

    added = service.add(manifest)
    reloaded = ExtensionCatalogService(
        ExtensionCatalogStorage(catalog_path), host_flavor_override="posix"
    ).load()
    assert added.entry.extension_id == "demo.extension"
    assert reloaded.get("demo.extension").manifest_path.persisted_value == manifest.as_posix()
    assert reloaded.get("demo.extension").manifest_path.flavor in {"posix", "windows"}
    assert len(reloaded.get("demo.extension").manifest_sha256) == 64
    assert service.validate()[0].state == "unchanged"

    manifest.write_text(json.dumps(_manifest(version="2.0.0")), encoding="utf-8")
    assert service.validate()[0].state == "changed"
    with pytest.raises(CatalogDriftError):
        service.replace("demo.extension", manifest, expected_fingerprint="0" * 64)
    service.replace("demo.extension", manifest, expected_fingerprint=added.entry.manifest_sha256)
    assert service.validate()[0].state == "unchanged"
    service.remove("demo.extension")
    assert ExtensionCatalogStorage(catalog_path).load().entries == ()
    assert manifest.exists()


def test_migration_acceptance_preserves_legacy_and_workspace_state(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text(json.dumps(_manifest("first.extension")), encoding="utf-8")
    second.write_text(json.dumps(_manifest("second.extension")), encoding="utf-8")
    legacy = tmp_path / "registry.json"
    legacy_bytes = json.dumps(
        {
            "first.extension": {"manifest_path": str(first), "enabled": True},
            "second.extension": {"manifest_path": str(second), "enabled": False},
        }
    ).encode()
    legacy.write_bytes(legacy_bytes)
    destination = tmp_path / "extensions" / "catalog.json"

    result = migrate_legacy(legacy, destination)

    assert len(result.entries) == 2
    assert destination.exists()
    assert legacy.read_bytes() == legacy_bytes
    assert not (tmp_path / "workspace" / "extensions.json").exists()
    assert migrate_legacy(legacy, destination) == result
