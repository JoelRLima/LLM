import json
from pathlib import Path

from agent.runtime.paths import AppPaths
from agent.runtime.workspace_context import WorkspaceContext
from agent.tools.extension_catalog_service import ExtensionCatalogService
from agent.tools.extension_catalog_storage import ExtensionCatalogStorage
from agent.tools.workspace_extensions_service import WorkspaceExtensionService


def _manifest(extension_id: str, capabilities: list[str]) -> dict[str, object]:
    return {
        "id": extension_id,
        "version": "1.0.0",
        "protocol_version": "1.0",
        "transport": "stdio",
        "entrypoint": ["python", "demo.py"],
        "timeout_seconds": 5,
        "tools": [{"name": "demo_tool", "schema": {}, "capabilities": capabilities}],
    }


def _write(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def test_workspace_grants_acceptance_and_manifest_drift(tmp_path: Path) -> None:
    manifest = tmp_path / "security.json"
    _write(manifest, _manifest("security.scanner", ["read", "process"]))
    workspace = tmp_path / "workspace-a"
    workspace.mkdir()
    paths = AppPaths.discover(tmp_path / "app", env={})
    catalog = ExtensionCatalogService(ExtensionCatalogStorage(paths.extensions_catalog_file))
    added = catalog.add(manifest)
    service = WorkspaceExtensionService.for_workspace(
        paths, WorkspaceContext.create(workspace).workspace_id, catalog
    )

    assert service.enable("security.scanner").selection.granted_capabilities == ()
    assert service.resolve().get("security.scanner").activation_status == "blocked"
    service.grant("security.scanner", "read")
    assert service.resolve().get("security.scanner").missing_grants == ("process",)
    service.grant("security.scanner", "process")
    assert service.resolve().get("security.scanner").activation_status == "ready"
    service.disable("security.scanner")
    assert service.resolve().get("security.scanner").activation_status == "disabled"
    service.enable("security.scanner")
    assert service.resolve().get("security.scanner").activation_status == "ready"
    service.revoke("security.scanner", "process")
    assert service.resolve().get("security.scanner").activation_status == "blocked"
    service.grant("security.scanner", "process")
    assert service.resolve().get("security.scanner").activation_status == "ready"

    before = service.load()
    _write(manifest, _manifest("security.scanner", ["read", "process", "network"]))
    drifted = service.resolve().get("security.scanner")
    assert drifted.activation_status == "blocked"
    assert service.load() == before
    assert service.load().get("security.scanner").granted_capabilities == ("process", "read")

    replaced = catalog.replace(
        "security.scanner",
        manifest,
        expected_fingerprint=added.entry.manifest_sha256,
    )
    assert replaced.changed is True
    assert service.resolve().get("security.scanner").missing_grants == ("network",)


def test_orphan_reference_and_two_workspace_isolation(tmp_path: Path) -> None:
    alpha = tmp_path / "alpha.json"
    beta = tmp_path / "beta.json"
    _write(alpha, _manifest("alpha.extension", ["read", "process"]))
    _write(beta, _manifest("beta.extension", ["write", "process"]))
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    paths = AppPaths.discover(tmp_path / "app", env={})
    catalog = ExtensionCatalogService(ExtensionCatalogStorage(paths.extensions_catalog_file))
    catalog.add(alpha)
    catalog.add(beta)
    first = WorkspaceExtensionService.for_workspace(
        paths, WorkspaceContext.create(first_root).workspace_id, catalog
    )
    second = WorkspaceExtensionService.for_workspace(
        paths, WorkspaceContext.create(second_root).workspace_id, catalog
    )
    first.enable("alpha.extension")
    second.enable("beta.extension")
    assert first.resolve().get("beta.extension") is None
    assert second.resolve().get("alpha.extension") is None
    first_bytes = first.workspace_paths.workspace_extensions_file.read_bytes()
    first.workspace_paths.workspace_extensions_file.write_bytes(b"corrupt")
    try:
        first.resolve()
    except Exception as exc:
        assert type(exc).__name__ == "WorkspaceConfigurationCorruptError"
    else:
        raise AssertionError("corrupt workspace configuration was accepted")
    assert second.resolve().get("beta.extension") is not None
    first.workspace_paths.workspace_extensions_file.write_bytes(first_bytes)
    first_state = first.load()
    catalog.remove("alpha.extension")
    assert first.load() == first_state
    assert first.workspace_paths.workspace_extensions_file.read_bytes() == first_bytes
    assert first.resolve().get("alpha.extension").catalog_presence == "orphaned"
    assert second.resolve().get("beta.extension").catalog_presence == "present"
    first.remove_configuration("alpha.extension")
    assert first.resolve().entries == ()
    assert alpha.exists()
