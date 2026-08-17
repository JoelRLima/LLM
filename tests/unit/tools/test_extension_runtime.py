import json
from pathlib import Path

import pytest

from agent.runtime.paths import AppPaths
from agent.runtime.workspace_context import WorkspaceContext
from agent.tools.extension_catalog_document import PersistedCatalogEntry
from agent.tools.extension_catalog_service import ExtensionCatalogService, host_path_flavor
from agent.tools.extension_catalog_storage import ExtensionCatalogStorage
from agent.tools.extension_path import PersistedManifestPath
from agent.tools.extension_runtime import ExtensionRuntimeBinding, ExtensionRuntimeMaterializer
from agent.tools.extension_state import fingerprint_for_bytes
from agent.tools.stdio_adapter import ExtensionManifest, StdioToolAdapter
from agent.tools.workspace_extensions_resolver import ResolvedWorkspaceExtension, ResolvedWorkspaceExtensions
from agent.tools.workspace_extensions_service import WorkspaceExtensionService


def _manifest(path: Path, *, entrypoint: list[str] | None = None, tool_name: str = "demo_tool") -> bytes:
    content = json.dumps(
        {
            "id": "demo.extension",
            "version": "1.0.0",
            "protocol_version": "1.0",
            "transport": "stdio",
            "entrypoint": entrypoint if entrypoint is not None else ["${python}", "${extension_dir}/demo.py"],
            "timeout_seconds": 5,
            "tools": [{"name": tool_name, "schema": {}, "capabilities": ["read", "process"]}],
        }
    ).encode("utf-8")
    path.write_bytes(content)
    return content


def _resolved(tmp_path: Path, *, entrypoint: list[str] | None = None):
    manifest = tmp_path / "extension" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    _manifest(manifest, entrypoint=entrypoint)
    paths = AppPaths.discover(tmp_path / "app", env={})
    catalog = ExtensionCatalogService(ExtensionCatalogStorage(paths.extensions_catalog_file))
    catalog.add(manifest)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace_id = WorkspaceContext.create(workspace).workspace_id
    service = WorkspaceExtensionService.for_workspace(paths, workspace_id, catalog)
    service.enable("demo.extension")
    service.grant("demo.extension", "read")
    service.grant("demo.extension", "process")
    return manifest, paths, workspace, catalog, service.resolve()


def test_ready_materializes_one_binding_and_expands_canonical_placeholders(tmp_path: Path) -> None:
    manifest, paths, workspace, catalog, resolved = _resolved(tmp_path)
    result = ExtensionRuntimeMaterializer(workspace, host_flavor=catalog.host_flavor).materialize(resolved)

    assert [binding.extension_id for binding in result.bindings] == ["demo.extension"]
    binding = result.bindings[0]
    assert binding.approved_fingerprint == fingerprint_for_bytes(manifest.read_bytes())
    assert binding.descriptors[0].name == "demo_tool"
    assert binding.adapter.cwd == workspace.resolve()
    assert binding.adapter.manifest.entrypoint[0] == __import__("sys").executable
    assert Path(binding.adapter.manifest.entrypoint[1]) == manifest.parent.resolve() / "demo.py"
    assert not result.diagnostics
    assert paths.extensions_catalog_file.exists()


def test_non_ready_entries_are_not_materialized(tmp_path: Path) -> None:
    manifest, _, workspace, catalog, resolved = _resolved(tmp_path)
    del manifest
    disabled = resolved.entries[0]
    blocked = ResolvedWorkspaceExtension(
        extension_id=disabled.extension_id,
        enabled=False,
        configured_grants=(),
        catalog_entry=disabled.catalog_entry,
        catalog_presence=disabled.catalog_presence,
        manifest_status=disabled.manifest_status,
        required_capabilities=disabled.required_capabilities,
        effective_grants=(),
        missing_grants=disabled.required_capabilities,
        unused_grants=(),
        activation_status="disabled",
    )
    result = ExtensionRuntimeMaterializer(workspace, host_flavor="posix").materialize(
        ResolvedWorkspaceExtensions((blocked,))
    )
    assert result.bindings == ()
    assert result.diagnostics[0].code == "EXTENSION_RUNTIME_NOT_ELIGIBLE"


def test_manifest_is_read_once_by_materializer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, _, workspace, catalog, resolved = _resolved(tmp_path)
    original = Path.read_bytes
    reads = 0

    def counted_read(path: Path) -> bytes:
        nonlocal reads
        if path == manifest:
            reads += 1
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", counted_read)
    result = ExtensionRuntimeMaterializer(workspace, host_flavor=catalog.host_flavor).materialize(resolved)
    assert result.bindings
    assert reads == 1


def test_drift_and_unknown_placeholder_fail_closed(tmp_path: Path) -> None:
    manifest, _, workspace, catalog, resolved = _resolved(tmp_path)
    manifest.write_bytes(manifest.read_bytes() + b"\n")
    drift = ExtensionRuntimeMaterializer(workspace, host_flavor=catalog.host_flavor).materialize(resolved)
    assert drift.bindings == ()
    assert drift.diagnostics[0].code == "EXTENSION_RUNTIME_FINGERPRINT_MISMATCH"

    manifest, _, workspace, catalog, resolved = _resolved(
        tmp_path / "placeholder", entrypoint=["${unknown}"]
    )
    del manifest
    placeholder = ExtensionRuntimeMaterializer(workspace, host_flavor=catalog.host_flavor).materialize(resolved)
    assert placeholder.bindings == ()
    assert placeholder.diagnostics[0].code == "EXTENSION_RUNTIME_PLACEHOLDER_UNKNOWN"

    _, _, workspace, catalog, resolved = _resolved(
        tmp_path / "incomplete", entrypoint=["prefix-${unknown"]
    )
    incomplete = ExtensionRuntimeMaterializer(workspace, host_flavor=catalog.host_flavor).materialize(resolved)
    assert incomplete.bindings == ()
    assert incomplete.diagnostics[0].code == "EXTENSION_RUNTIME_PLACEHOLDER_INVALID"


def test_placeholder_expansion_is_single_pass_and_preserves_literal_replacements(tmp_path: Path) -> None:
    extension_dir = tmp_path / "base" / "${python}"
    expanded = ExtensionRuntimeMaterializer._expand_entrypoint(
        ("--path=${extension_dir}/file", "${extension_dir}", "${python}"),
        extension_dir,
    )

    assert expanded[0] == f"--path={extension_dir.absolute()}/file"
    assert expanded[1] == str(extension_dir.absolute())
    assert expanded[2]
    assert "${python}" in expanded[1]
    with pytest.raises(ValueError):
        ExtensionRuntimeMaterializer._expand_entrypoint(("${}",), extension_dir)
    with pytest.raises(ValueError):
        ExtensionRuntimeMaterializer._expand_entrypoint(("${nested${python}}",), extension_dir)


def test_runtime_binding_and_stdio_configuration_are_deeply_immutable(tmp_path: Path) -> None:
    metadata = {"nested": {"values": ["original"]}}
    binding = ExtensionRuntimeBinding(
        "demo.extension",
        "0" * 64,
        object(),
        (),
        metadata,
    )
    metadata["nested"]["values"].append("external")
    assert binding.metadata["nested"]["values"] == ["original"]
    first = binding.metadata
    second = binding.metadata
    dict.__setitem__(first["nested"], "injected", True)
    first["nested"]["values"].append("changed")
    assert second == {"nested": {"values": ["original"]}}
    assert binding.metadata == second

    original_tools = ({"name": "demo", "schema": {"nested": {"values": [1]}}, "capabilities": ["read", "process"]},)
    original_entrypoint = ["python", "demo.py"]
    manifest = ExtensionManifest(
        id="demo.extension",
        version="1",
        protocol_version="1.0",
        transport="stdio",
        entrypoint=original_entrypoint,
        timeout_seconds=5,
        tools=original_tools,
    )
    adapter = StdioToolAdapter(manifest, cwd=tmp_path)
    original_tools[0]["schema"]["nested"]["values"].append(2)
    original_entrypoint[0] = "changed"

    assert adapter.manifest.entrypoint == ("python", "demo.py")
    assert adapter.descriptors()[0].schema["nested"]["values"] == [1]
    with pytest.raises(AttributeError):
        adapter.cwd = tmp_path / "other"
    with pytest.raises(AttributeError):
        adapter.manifest = manifest


def test_invalid_entrypoint_uses_canonical_manifest_invalid_diagnostic(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    content = json.dumps(
        {
            "id": "demo.extension",
            "version": "1.0.0",
            "protocol_version": "1.0",
            "transport": "stdio",
            "entrypoint": [],
            "timeout_seconds": 5,
            "tools": [{"name": "demo_tool", "schema": {}, "capabilities": []}],
        }
    ).encode("utf-8")
    manifest.write_bytes(content)
    flavor = host_path_flavor()
    entry = PersistedCatalogEntry(
        "demo.extension",
        PersistedManifestPath(manifest.as_posix(), flavor),
        fingerprint_for_bytes(content),
    )
    resolved = ResolvedWorkspaceExtensions(
        (
            ResolvedWorkspaceExtension(
                extension_id="demo.extension",
                enabled=True,
                configured_grants=(),
                catalog_entry=entry,
                catalog_presence="present",
                manifest_status="unchanged",
                required_capabilities=(),
                effective_grants=(),
                missing_grants=(),
                unused_grants=(),
                activation_status="ready",
            ),
        )
    )
    result = ExtensionRuntimeMaterializer(tmp_path, host_flavor=flavor).materialize(resolved)

    assert result.bindings == ()
    assert result.diagnostics[0].code == "EXTENSION_RUNTIME_MANIFEST_INVALID"


def test_failure_in_one_tool_rejects_the_entire_extension(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, _, workspace, catalog, resolved = _resolved(tmp_path)

    def fail_descriptors(_adapter):
        raise ValueError("second tool failed")

    monkeypatch.setattr("agent.tools.extension_runtime.StdioToolAdapter.descriptors", fail_descriptors)
    result = ExtensionRuntimeMaterializer(workspace, host_flavor=catalog.host_flavor).materialize(resolved)
    assert result.bindings == ()
    assert result.diagnostics[0].code == "EXTENSION_RUNTIME_DESCRIPTOR_INVALID"
