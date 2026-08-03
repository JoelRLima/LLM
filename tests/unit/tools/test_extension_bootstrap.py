import json
from pathlib import Path

import pytest

from agent.runtime.config_repository import ConfigRepository
from agent.runtime.paths import AppPaths
from agent.runtime.workspace_context import WorkspaceContext
from agent.skills import load_skill_registry
from agent.tools.builtin_adapter import BuiltinToolAdapter
from agent.tools.contracts import ToolDescriptor, ToolResult
from agent.tools.extension_bootstrap import ApplicationExtensionBootstrap, WorkspaceToolRegistryComposer
from agent.tools.extension_catalog_service import ExtensionCatalogService
from agent.tools.extension_catalog_storage import ExtensionCatalogStorage
from agent.tools.extension_runtime import (
    ExtensionRuntimeBinding,
    ExtensionRuntimeMaterialization,
)
from agent.tools.workspace_extensions_service import WorkspaceExtensionService


class _Adapter:
    def __init__(self, descriptors: tuple[ToolDescriptor, ...]) -> None:
        self._descriptors = descriptors

    def descriptors(self) -> tuple[ToolDescriptor, ...]:
        return self._descriptors

    def invoke(self, invocation):
        return ToolResult(invocation_id=invocation.invocation_id, status="succeeded")


def _descriptor(name: str) -> ToolDescriptor:
    return ToolDescriptor(name=name, description="safe", schema={}, adapter_id="demo.extension")


def _binding(extension_id: str, *names: str) -> ExtensionRuntimeBinding:
    descriptors = tuple(_descriptor(name) for name in names)
    return ExtensionRuntimeBinding(
        extension_id=extension_id,
        approved_fingerprint="0" * 64,
        adapter=_Adapter(descriptors),
        descriptors=descriptors,
    )


def test_composer_preserves_builtins_and_rejects_all_colliding_extensions(tmp_path: Path) -> None:
    builtin = BuiltinToolAdapter(load_skill_registry(base_dir=tmp_path))
    materialization = ExtensionRuntimeMaterialization(
        bindings=(_binding("alpha.extension", "echo"), _binding("beta.extension", "shared"), _binding("gamma.extension", "shared"), _binding("safe.extension", "unique"))
    )
    result = WorkspaceToolRegistryComposer().compose(builtin, materialization)

    assert "echo" in result.registry.names()
    assert "unique" in result.registry.names()
    assert "shared" not in result.registry.names()
    assert {item.extension_id for item in result.diagnostics if item.code == "EXTENSION_RUNTIME_COLLISION"} == {
        "alpha.extension",
        "beta.extension",
        "gamma.extension",
    }
    assert result.registry.frozen is True
    with pytest.raises(RuntimeError):
        result.registry.register_adapter(_Adapter((_descriptor("later"),)))


def test_mixed_collisions_reject_every_extension_participant(tmp_path: Path) -> None:
    builtin = BuiltinToolAdapter(load_skill_registry(base_dir=tmp_path))
    result = WorkspaceToolRegistryComposer().compose(
        builtin,
        ExtensionRuntimeMaterialization(
            bindings=(
                _binding("alpha.extension", "echo", "shared"),
                _binding("beta.extension", "shared"),
                _binding("gamma.extension", "unique"),
            )
        ),
    )

    assert "echo" in result.registry.names()
    assert "unique" in result.registry.names()
    assert "shared" not in result.registry.names()
    assert {
        diagnostic.extension_id
        for diagnostic in result.diagnostics
        if diagnostic.code == "EXTENSION_RUNTIME_COLLISION"
    } == {"alpha.extension", "beta.extension"}


def test_internal_and_external_collision_rejects_both_extensions(tmp_path: Path) -> None:
    builtin = BuiltinToolAdapter(load_skill_registry(base_dir=tmp_path))
    result = WorkspaceToolRegistryComposer().compose(
        builtin,
        ExtensionRuntimeMaterialization(
            bindings=(
                _binding("alpha.extension", "dup", "dup", "shared"),
                _binding("beta.extension", "shared"),
            )
        ),
    )

    assert all(name not in result.registry.names() for name in ("dup", "shared"))
    assert {
        diagnostic.extension_id
        for diagnostic in result.diagnostics
        if diagnostic.code == "EXTENSION_RUNTIME_COLLISION"
    } == {"alpha.extension", "beta.extension"}


def test_collision_result_is_independent_of_binding_order(tmp_path: Path) -> None:
    builtin = BuiltinToolAdapter(load_skill_registry(base_dir=tmp_path))
    first = WorkspaceToolRegistryComposer().compose(
        builtin,
        ExtensionRuntimeMaterialization(
            bindings=(
                _binding("alpha.extension", "echo", "shared"),
                _binding("beta.extension", "shared"),
                _binding("gamma.extension", "unique"),
            )
        ),
    )
    second = WorkspaceToolRegistryComposer().compose(
        builtin,
        ExtensionRuntimeMaterialization(
            bindings=(
                _binding("gamma.extension", "unique"),
                _binding("beta.extension", "shared"),
                _binding("alpha.extension", "echo", "shared"),
            )
        ),
    )

    assert first.registry.names() == second.registry.names()
    assert tuple(d.to_dict() for d in first.diagnostics) == tuple(
        d.to_dict() for d in second.diagnostics
    )


def test_bootstrap_missing_files_is_builtin_only_and_does_not_execute_processes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = AppPaths.discover(tmp_path / "app", env={})
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    registry = load_skill_registry(base_dir=workspace)
    builtin = BuiltinToolAdapter(registry)

    def forbidden(*args, **kwargs):
        raise AssertionError("bootstrap iniciou processo")

    monkeypatch.setattr("subprocess.Popen", forbidden)
    monkeypatch.setattr("subprocess.run", forbidden)
    monkeypatch.setattr("os.system", forbidden)
    result = ApplicationExtensionBootstrap(paths, WorkspaceContext.create(workspace).workspace_id, workspace).build(builtin)

    assert "echo" in result.registry.names()
    assert result.materialization.bindings == ()
    assert result.diagnostics == ()
    assert not paths.extensions_catalog_lock_file.exists()


def test_corrupt_catalog_degrades_without_treating_it_as_empty(tmp_path: Path) -> None:
    paths = AppPaths.discover(tmp_path / "app", env={})
    ConfigRepository(paths).initialize()
    paths.extensions_catalog_file.parent.mkdir(parents=True, exist_ok=True)
    paths.extensions_catalog_file.write_text("{", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    builtin = BuiltinToolAdapter(load_skill_registry(base_dir=workspace))
    result = ApplicationExtensionBootstrap(paths, WorkspaceContext.create(workspace).workspace_id, workspace).build(builtin)

    assert "echo" in result.registry.names()
    assert result.materialization.bindings == ()
    assert result.diagnostics[0].code == "EXTENSION_BOOTSTRAP_CATALOG_CORRUPT"


def test_corrupt_workspace_degrades_with_explicit_diagnostic(tmp_path: Path) -> None:
    paths = AppPaths.discover(tmp_path / "app", env={})
    ConfigRepository(paths).initialize()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace_id = WorkspaceContext.create(workspace).workspace_id
    workspace_file = paths.for_workspace(workspace_id).workspace_extensions_file
    workspace_file.parent.mkdir(parents=True, exist_ok=True)
    workspace_file.write_text("{", encoding="utf-8")
    builtin = BuiltinToolAdapter(load_skill_registry(base_dir=workspace))

    result = ApplicationExtensionBootstrap(paths, workspace_id, workspace).build(builtin)

    assert "echo" in result.registry.names()
    assert result.materialization.bindings == ()
    assert result.diagnostics[0].code == "EXTENSION_BOOTSTRAP_WORKSPACE_CORRUPT"


@pytest.mark.parametrize("error", [AssertionError, AttributeError, TypeError, RuntimeError])
def test_unexpected_bootstrap_errors_propagate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: type[Exception]
) -> None:
    paths = AppPaths.discover(tmp_path / "app", env={})
    ConfigRepository(paths).initialize()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    builtin = BuiltinToolAdapter(load_skill_registry(base_dir=workspace))

    monkeypatch.setattr(
        "agent.tools.extension_catalog_storage.ExtensionCatalogStorage.load",
        lambda _storage: (_ for _ in ()).throw(error("unexpected")),
    )

    with pytest.raises(error):
        ApplicationExtensionBootstrap(paths, WorkspaceContext.create(workspace).workspace_id, workspace).build(builtin)


def test_workspace_bootstrap_isolated_and_old_snapshot_is_immutable(tmp_path: Path) -> None:
    paths = AppPaths.discover(tmp_path / "app", env={})
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "id": "demo.extension", "version": "1", "protocol_version": "1.0", "transport": "stdio",
        "entrypoint": ["${python}", "demo.py"], "timeout_seconds": 5,
        "tools": [{"name": "demo_tool", "schema": {}, "capabilities": ["read"]}],
    }), encoding="utf-8")
    catalog = ExtensionCatalogService(ExtensionCatalogStorage(paths.extensions_catalog_file))
    catalog.add(manifest)
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    first_id = WorkspaceContext.create(first_root).workspace_id
    second_id = WorkspaceContext.create(second_root).workspace_id
    first_service = WorkspaceExtensionService.for_workspace(paths, first_id, catalog)
    first_service.enable("demo.extension")
    first_service.grant("demo.extension", "read")
    builtin = BuiltinToolAdapter(load_skill_registry(base_dir=first_root))
    first = ApplicationExtensionBootstrap(paths, first_id, first_root).build(builtin)
    second_builtin = BuiltinToolAdapter(load_skill_registry(base_dir=second_root))
    second = ApplicationExtensionBootstrap(paths, second_id, second_root).build(second_builtin)

    assert "demo_tool" in first.registry.names()
    assert "demo_tool" not in second.registry.names()
    assert first.authority is not None
    assert first.registry.runtime_identity is first.authority.runtime_identity
    assert first.authority.extension_grants["demo.extension"] == frozenset({"read"})
    assert second.authority is not None
    assert second.authority.extension_grants == {}
    first_service.disable("demo.extension")
    assert "demo_tool" in first.registry.names()
