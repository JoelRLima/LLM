from __future__ import annotations

import json
from pathlib import Path

import pytest

from llm_agent.application import legacy_extension_registry as application
from llm_agent.application.context import AppPaths
from llm_agent.application.legacy_extension_registry import (
    add_legacy_extension,
    doctor_legacy_extensions,
    list_legacy_extensions,
    set_legacy_extension_enabled,
)
from llm_agent.extensions.extension_manifest_parser import ManifestParseError
from llm_agent.extensions.extension_registry import ExtensionRegistry, ExtensionState
from llm_agent.workspace.context import WorkspaceContext


def _app_paths(tmp_path: Path) -> AppPaths:
    return AppPaths.discover(tmp_path / "home")


def _manifest(extension_id: str) -> bytes:
    return json.dumps(
        {
            "id": extension_id,
            "version": "1.2.3",
            "protocol_version": "1.0",
            "transport": "stdio",
            "entrypoint": ["python", "extension.py"],
            "timeout_seconds": 20,
            "tools": [{"name": "sample", "capabilities": ["process"]}],
        }
    ).encode()


def test_legacy_reads_of_missing_registry_do_not_create_files_or_directories(tmp_path: Path) -> None:
    app_paths = _app_paths(tmp_path)

    assert list_legacy_extensions(app_paths) == ()
    assert list(doctor_legacy_extensions(app_paths)) == []
    assert not app_paths.extensions_registry_file.exists()
    assert not app_paths.extensions_registry_file.parent.exists()


def test_legacy_add_list_enable_disable_and_unknown_id_parity(tmp_path: Path) -> None:
    app_paths = _app_paths(tmp_path)
    manifest_path = tmp_path / "missing-manifest.json"

    added = add_legacy_extension(app_paths, "sample", manifest_path, enabled=False)

    assert added.id == "sample"
    assert added.manifest_path == manifest_path.resolve()
    assert added.enabled is False
    assert [(item.id, item.enabled) for item in list_legacy_extensions(app_paths)] == [("sample", False)]
    assert set_legacy_extension_enabled(app_paths, "sample", True).enabled is True
    assert set_legacy_extension_enabled(app_paths, "sample", False).enabled is False
    with pytest.raises(KeyError, match="Extensão não registrada: absent"):
        set_legacy_extension_enabled(app_paths, "absent", True)


def test_legacy_doctor_projects_missing_manifest(tmp_path: Path) -> None:
    app_paths = _app_paths(tmp_path)
    add_legacy_extension(app_paths, "missing", tmp_path / "absent.json")

    result = next(doctor_legacy_extensions(app_paths))

    assert result.id == "missing"
    assert result.manifest_status == "missing"
    assert result.manifest_id is None
    assert result.manifest_version is None


def test_legacy_add_preserves_manifest_validation_and_close_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app_paths = _app_paths(tmp_path)
    manifest = tmp_path / "wrong.json"
    manifest.write_bytes(_manifest("different"))
    close_calls: list[str] = []

    class Lease:
        def close(self) -> None:
            close_calls.append("close")

    monkeypatch.setattr(application.HomeLifecycleLease, "begin_transient", lambda _home: Lease())
    monkeypatch.setattr(application.StorageBootstrap, "prepare", lambda _self, _paths: None)

    with pytest.raises(ValueError, match="manifest.id não corresponde ao ID registrado"):
        add_legacy_extension(app_paths, "requested", manifest)
    assert close_calls == ["close"]


def test_canonical_mutation_order_and_close_on_bootstrap_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app_paths = _app_paths(tmp_path)
    events: list[str] = []
    real_registry = ExtensionRegistry

    class Lease:
        def close(self) -> None:
            events.append("close")

    class Bootstrap:
        def prepare(self, _paths: AppPaths) -> None:
            events.append("bootstrap")
            raise RuntimeError("bootstrap failed")

    def begin_transient(_home: Path) -> Lease:
        events.append("lease")
        return Lease()

    def registry_factory(target: str | Path) -> ExtensionRegistry:
        events.append("registry")
        return real_registry(target)

    monkeypatch.setattr(application.HomeLifecycleLease, "begin_transient", begin_transient)
    monkeypatch.setattr(application, "StorageBootstrap", Bootstrap)
    monkeypatch.setattr(application, "ExtensionRegistry", registry_factory)

    with pytest.raises(RuntimeError, match="bootstrap failed"):
        add_legacy_extension(app_paths, "sample", tmp_path / "missing.json")
    assert events == ["lease", "bootstrap", "close"]


def test_canonical_mutation_loads_after_bootstrap_and_closes_after_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app_paths = _app_paths(tmp_path)
    events: list[str] = []
    real_registry = ExtensionRegistry

    class Lease:
        def close(self) -> None:
            events.append("close")

    class Bootstrap:
        def prepare(self, _paths: AppPaths) -> None:
            events.append("bootstrap")

    class Registry:
        def __init__(self, target: str | Path) -> None:
            events.append("registry-load")
            self._registry = real_registry(target)

        def add(
            self,
            *,
            id: str,
            manifest_path: str | Path,
            enabled: bool = True,
        ) -> ExtensionState:
            events.append("mutation")
            return self._registry.add(id=id, manifest_path=manifest_path, enabled=enabled)

    monkeypatch.setattr(application.HomeLifecycleLease, "begin_transient", lambda _home: (events.append("lease") or Lease()))
    monkeypatch.setattr(application, "StorageBootstrap", Bootstrap)
    monkeypatch.setattr(application, "ExtensionRegistry", Registry)

    add_legacy_extension(app_paths, "sample", tmp_path / "missing.json")

    assert events == ["lease", "bootstrap", "registry-load", "mutation", "close"]


def test_external_mutation_skips_canonical_guards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app_paths = _app_paths(tmp_path)

    def unexpected(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("external legacy mutation acquired canonical lifecycle")

    monkeypatch.setattr(application.HomeLifecycleLease, "begin_transient", unexpected)
    monkeypatch.setattr(application, "StorageBootstrap", unexpected)
    state_path = tmp_path / "external" / "registry.json"
    add_legacy_extension(app_paths, "sample", tmp_path / "missing.json", state_path=state_path)
    assert ExtensionRegistry(state_path).get("sample") is not None


def test_custom_registry_inside_home_uses_canonical_guards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app_paths = _app_paths(tmp_path)
    events: list[str] = []

    class Lease:
        def close(self) -> None:
            events.append("close")

    class Bootstrap:
        def prepare(self, _paths: AppPaths) -> None:
            events.append("bootstrap")

    monkeypatch.setattr(
        application.HomeLifecycleLease,
        "begin_transient",
        lambda _home: (events.append("lease") or Lease()),
    )
    monkeypatch.setattr(application, "StorageBootstrap", Bootstrap)
    custom_state = app_paths.home_dir / "custom" / "registry.json"

    add_legacy_extension(app_paths, "inside", tmp_path / "missing.json", state_path=custom_state)

    assert events == ["lease", "bootstrap", "close"]
    assert ExtensionRegistry(custom_state).get("inside") is not None


def test_doctor_is_lazy_and_keeps_prior_diagnostics_consumable(tmp_path: Path) -> None:
    app_paths = _app_paths(tmp_path)
    good_manifest = tmp_path / "good.json"
    good_manifest.write_bytes(_manifest("a-good"))
    bad_manifest = tmp_path / "bad.json"
    add_legacy_extension(app_paths, "a-good", good_manifest)
    add_legacy_extension(app_paths, "b-bad", bad_manifest)
    bad_manifest.write_bytes(b"{")

    diagnostics = doctor_legacy_extensions(app_paths)
    first = next(diagnostics)
    assert (first.id, first.manifest_status, first.manifest_id, first.manifest_version) == (
        "a-good", "ok", "a-good", "1.2.3"
    )
    with pytest.raises(ManifestParseError):
        next(diagnostics)


def test_canonical_registry_load_failure_closes_acquired_lease(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app_paths = _app_paths(tmp_path)
    app_paths.extensions_registry_file.parent.mkdir(parents=True, exist_ok=True)
    app_paths.extensions_registry_file.write_text("[]", encoding="utf-8")
    close_calls: list[str] = []

    class Lease:
        def close(self) -> None:
            close_calls.append("close")

    monkeypatch.setattr(application.HomeLifecycleLease, "begin_transient", lambda _home: Lease())
    monkeypatch.setattr(application.StorageBootstrap, "prepare", lambda _self, _paths: None)

    with pytest.raises(ValueError, match="Registro de extensões inválido"):
        add_legacy_extension(app_paths, "sample", tmp_path / "missing.json")
    assert close_calls == ["close"]


def test_canonical_registry_write_failure_closes_acquired_lease(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app_paths = _app_paths(tmp_path)
    close_calls: list[str] = []

    class Lease:
        def close(self) -> None:
            close_calls.append("close")

    monkeypatch.setattr(application.HomeLifecycleLease, "begin_transient", lambda _home: Lease())
    monkeypatch.setattr(application.StorageBootstrap, "prepare", lambda _self, _paths: None)
    monkeypatch.setattr(
        application.ExtensionRegistry,
        "add",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("write failed")),
    )

    with pytest.raises(RuntimeError, match="write failed"):
        add_legacy_extension(app_paths, "sample", tmp_path / "missing.json")
    assert close_calls == ["close"]


def test_legacy_mutation_does_not_modify_modern_catalog_or_workspace_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app_paths = _app_paths(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace_paths = app_paths.for_workspace(WorkspaceContext.create(workspace).workspace_id)
    catalog = app_paths.extensions_catalog_file
    modern_state = workspace_paths.workspace_extensions_file
    catalog.parent.mkdir(parents=True, exist_ok=True)
    modern_state.parent.mkdir(parents=True, exist_ok=True)
    catalog.write_text('{"modern":"catalog"}', encoding="utf-8")
    modern_state.write_text('{"modern":"workspace"}', encoding="utf-8")
    before = (catalog.read_bytes(), modern_state.read_bytes())
    monkeypatch.setattr(application.HomeLifecycleLease, "begin_transient", lambda _home: type("Lease", (), {"close": lambda _self: None})())
    monkeypatch.setattr(application.StorageBootstrap, "prepare", lambda _self, _paths: None)

    add_legacy_extension(app_paths, "legacy", tmp_path / "missing.json")

    assert (catalog.read_bytes(), modern_state.read_bytes()) == before
