import inspect
import json
from pathlib import Path

import pytest

from agent.runtime.paths import AppPaths
from agent.runtime.workspace_context import WorkspaceContext
from agent.tools.extension_catalog_errors import (
    WorkspaceCapabilityInvalidError,
    WorkspaceCapabilityNotDeclaredError,
    WorkspaceExtensionMissingError,
    WorkspaceExtensionNotConfiguredError,
    WorkspaceManifestBlockedError,
    WorkspacePathError,
)
from agent.tools.extension_catalog_lock import ExtensionCatalogLock
from agent.tools.extension_catalog_service import ExtensionCatalogService
from agent.tools.extension_catalog_storage import ExtensionCatalogStorage
from agent.tools.workspace_extensions_service import WorkspaceExtensionService
from agent.tools.workspace_extensions_storage import WorkspaceExtensionsStorage


def _manifest(extension_id: str = "demo.extension", capabilities: list[str] | None = None) -> dict:
    declared = list(capabilities or [])
    if "process" not in declared:
        declared.append("process")
    return {
        "id": extension_id,
        "version": "1.0.0",
        "protocol_version": "1.0",
        "transport": "stdio",
        "entrypoint": ["python", "demo.py"],
        "timeout_seconds": 5,
        "tools": [{"name": "demo_tool", "schema": {}, "capabilities": declared}],
    }


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _services(tmp_path: Path, *, extension_id: str = "demo.extension", capabilities=None):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    manifest = tmp_path / "manifest.json"
    _write(manifest, _manifest(extension_id, capabilities or ["read", "process"]))
    paths = AppPaths.discover(tmp_path / "app", env={})
    workspace_paths = paths.for_workspace(WorkspaceContext.create(workspace_root).workspace_id)
    catalog = ExtensionCatalogService(ExtensionCatalogStorage(paths.extensions_catalog_file))
    catalog.add(manifest)
    return manifest, catalog, WorkspaceExtensionService.for_workspace(
        paths, workspace_paths.workspace_id, catalog
    )


def test_enable_never_auto_grants_and_grant_is_explicit(tmp_path: Path) -> None:
    _, _, service = _services(tmp_path)
    enabled = service.enable("demo.extension")
    assert enabled.changed is True
    assert enabled.selection.granted_capabilities == ()
    assert service.resolve().get("demo.extension").activation_status == "blocked"
    with pytest.raises(WorkspaceCapabilityNotDeclaredError):
        service.grant("demo.extension", "network")
    assert service.grant("demo.extension", "read").changed is True
    assert service.resolve().get("demo.extension").missing_grants == ("process",)
    assert service.grant("demo.extension", "process").changed is True
    assert service.resolve().get("demo.extension").activation_status == "ready"
    assert service.grant("demo.extension", "process").changed is False


def test_disable_preserves_grants_and_reenable_restores_readiness(tmp_path: Path) -> None:
    _, _, service = _services(tmp_path, capabilities=["read"])
    service.enable("demo.extension")
    service.grant("demo.extension", "read")
    service.grant("demo.extension", "process")
    assert service.disable("demo.extension").changed is True
    disabled = service.resolve().get("demo.extension")
    assert disabled.activation_status == "disabled"
    assert disabled.configured_grants == ("process", "read")
    assert service.enable("demo.extension").changed is True
    assert service.resolve().get("demo.extension").activation_status == "ready"


def test_idempotent_operations_do_not_save_again(tmp_path: Path) -> None:
    manifest, catalog, service = _services(tmp_path, capabilities=["read"])
    calls = 0
    original = service.storage.save

    def save(state):
        nonlocal calls
        calls += 1
        original(state)

    service.storage.save = save  # type: ignore[method-assign]
    service.enable("demo.extension")
    service.enable("demo.extension")
    service.grant("demo.extension", "read")
    service.grant("demo.extension", "read")
    assert calls == 2
    assert manifest.exists() and catalog.load().get("demo.extension") is not None


def test_disable_and_revoke_absent_are_idempotent(tmp_path: Path) -> None:
    _, _, service = _services(tmp_path)
    assert service.disable("demo.extension").changed is False
    assert service.revoke("demo.extension", "read").changed is False
    with pytest.raises(WorkspaceExtensionNotConfiguredError):
        service.grant("demo.extension", "read")


def test_invalid_capability_is_rejected_without_mutation(tmp_path: Path) -> None:
    _, _, service = _services(tmp_path)
    service.enable("demo.extension")
    with pytest.raises(WorkspaceCapabilityInvalidError):
        service.grant("demo.extension", "")


def test_manifest_drift_blocks_grant_but_preserves_workspace_state(tmp_path: Path) -> None:
    manifest, _, service = _services(tmp_path, capabilities=["read"])
    service.enable("demo.extension")
    original = service.load()
    _write(manifest, _manifest(capabilities=["read", "process", "network"]))
    assert service.resolve().get("demo.extension").activation_status == "blocked"
    with pytest.raises(WorkspaceManifestBlockedError):
        service.grant("demo.extension", "process")
    assert service.load() == original


def test_enable_requires_catalog_entry_and_remove_configuration_is_explicit(tmp_path: Path) -> None:
    _, _, service = _services(tmp_path)
    with pytest.raises(WorkspaceExtensionMissingError):
        service.enable("missing.extension")
    service.enable("demo.extension")
    assert service.remove_configuration("demo.extension").changed is True
    assert service.resolve().entries == ()
    assert service.remove_configuration("demo.extension").changed is False


def test_orphan_is_preserved_until_explicit_remove(tmp_path: Path) -> None:
    _, catalog, service = _services(tmp_path, capabilities=["read"])
    service.enable("demo.extension")
    before = service.load()
    catalog.remove("demo.extension")
    resolved = service.resolve().get("demo.extension")
    assert service.load() == before
    assert resolved.catalog_presence == "orphaned"
    assert resolved.activation_status == "blocked"
    assert any(item.code == "WORKSPACE_EXTENSION_ORPHANED" for item in resolved.diagnostics)
    service.remove_configuration("demo.extension")
    assert service.load().selections == ()


def test_inspect_reports_expected_corruption_but_propagates_programming_errors(tmp_path: Path) -> None:
    _, catalog, service = _services(tmp_path)
    service.storage.path.parent.mkdir(parents=True, exist_ok=True)
    service.storage.path.write_bytes(b"not-json")
    state, diagnostics = service.inspect()
    assert state is None
    assert diagnostics[0].message == "Configuração do workspace inválida."

    class ExplodingStorage:
        path = service.workspace_paths.workspace_extensions_file

        def load(self):
            raise AssertionError("programmer bug")

    broken = WorkspaceExtensionService._for_testing(
        service._app_paths,
        service._workspace_id,
        catalog,
        storage=ExplodingStorage(),  # type: ignore[arg-type]
    )
    with pytest.raises(AssertionError, match="programmer bug"):
        broken.inspect()


def test_two_workspaces_have_independent_files_locks_and_results(tmp_path: Path) -> None:
    manifest_a = tmp_path / "a.json"
    manifest_b = tmp_path / "b.json"
    _write(manifest_a, _manifest("alpha.extension", ["read"]))
    _write(manifest_b, _manifest("beta.extension", ["write"]))
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    paths = AppPaths.discover(tmp_path / "app", env={})
    catalog = ExtensionCatalogService(ExtensionCatalogStorage(paths.extensions_catalog_file))
    catalog.add(manifest_a)
    catalog.add(manifest_b)
    a_paths = paths.for_workspace(WorkspaceContext.create(workspace_a).workspace_id)
    b_paths = paths.for_workspace(WorkspaceContext.create(workspace_b).workspace_id)
    first = WorkspaceExtensionService.for_workspace(paths, a_paths.workspace_id, catalog)
    second = WorkspaceExtensionService.for_workspace(paths, b_paths.workspace_id, catalog)
    first.enable("alpha.extension")
    second.enable("beta.extension")
    assert first.workspace_paths.workspace_extensions_file != second.workspace_paths.workspace_extensions_file
    assert first.workspace_paths.workspace_extensions_lock_file != second.workspace_paths.workspace_extensions_lock_file
    assert first.resolve().get("alpha.extension") is not None
    assert first.resolve().get("beta.extension") is None
    assert second.resolve().get("beta.extension") is not None
    assert second.resolve().get("alpha.extension") is None


def test_two_services_for_same_workspace_share_file_and_lock(tmp_path: Path) -> None:
    paths = AppPaths.discover(tmp_path / "app", env={})
    manifest = tmp_path / "manifest.json"
    _write(manifest, _manifest("demo.extension", ["read"]))
    catalog = ExtensionCatalogService(ExtensionCatalogStorage(paths.extensions_catalog_file))
    catalog.add(manifest)
    first = WorkspaceExtensionService.for_workspace(paths, "same-workspace", catalog)
    second = WorkspaceExtensionService.for_workspace(paths, "same-workspace", catalog)
    assert first.workspace_paths.workspace_extensions_file == second.workspace_paths.workspace_extensions_file
    assert first.workspace_paths.workspace_extensions_lock_file == second.workspace_paths.workspace_extensions_lock_file
    first.enable("demo.extension")
    assert second.load() == first.load()


def test_operational_factory_is_canonical_and_relative_injection_fails_before_io(
    tmp_path: Path,
) -> None:
    assert tuple(inspect.signature(WorkspaceExtensionService).parameters) == (
        "app_paths",
        "workspace_id",
        "catalog_service",
    )
    assert tuple(inspect.signature(WorkspaceExtensionService.for_workspace).parameters) == (
        "app_paths",
        "workspace_id",
        "catalog_service",
    )
    paths = AppPaths.discover(tmp_path / "app", env={})
    manifest = tmp_path / "manifest.json"
    _write(manifest, _manifest("demo.extension", ["read"]))
    catalog = ExtensionCatalogService(ExtensionCatalogStorage(paths.extensions_catalog_file))
    catalog.add(manifest)
    first = WorkspaceExtensionService.for_workspace(paths, "workspace-a", catalog)
    second = WorkspaceExtensionService.for_workspace(paths, "workspace-b", catalog)
    again = WorkspaceExtensionService.for_workspace(paths, "workspace-a", catalog)
    assert first.workspace_paths.workspace_extensions_file != second.workspace_paths.workspace_extensions_file
    assert first.workspace_paths.workspace_extensions_lock_file != second.workspace_paths.workspace_extensions_lock_file
    assert first.workspace_paths.workspace_extensions_file == again.workspace_paths.workspace_extensions_file
    with pytest.raises(WorkspacePathError):
        WorkspaceExtensionService._for_testing(
            paths,
            "workspace-a",
            catalog,
            storage=WorkspaceExtensionsStorage(tmp_path / "other.json"),
        )
    with pytest.raises(WorkspacePathError):
        WorkspaceExtensionService._for_testing(
            paths,
            "workspace-a",
            catalog,
            lock=ExtensionCatalogLock(tmp_path / "other.lock"),
        )
