import pytest

from agent.tools.extension_catalog_document import ExtensionCatalogDocument, PersistedCatalogEntry
from agent.tools.extension_catalog_validation import ManifestObservation, ManifestSummary
from agent.tools.extension_path import PersistedManifestPath
from agent.tools.extension_state import WorkspaceExtensionSelection, WorkspaceExtensionsState
from agent.tools.workspace_extensions_resolver import resolve_workspace_extensions


def _observation(extension_id: str, status: str, capabilities: tuple[str, ...]) -> ManifestObservation:
    return ManifestObservation(extension_id, status, "f" * 64, ManifestSummary(capabilities))


def test_disabled_and_orphaned_keep_separate_dimensions() -> None:
    state = WorkspaceExtensionsState(
        (WorkspaceExtensionSelection("missing.extension", ("read",), False),)
    )
    resolved = resolve_workspace_extensions(state, ExtensionCatalogDocument(), ())
    entry = resolved.get("missing.extension")
    assert entry.catalog_presence == "orphaned"
    assert entry.activation_status == "disabled"
    assert {item.code for item in entry.diagnostics} == {
        "WORKSPACE_EXTENSION_ORPHANED",
        "WORKSPACE_EXTENSION_DISABLED",
    }


def test_changed_manifest_blocks_without_mutating_inputs() -> None:
    selection = WorkspaceExtensionSelection("demo.extension", ("read",), True)
    state = WorkspaceExtensionsState((selection,))
    observed = (_observation("demo.extension", "changed", ("read", "process")),)
    result = resolve_workspace_extensions(state, ExtensionCatalogDocument(), observed)
    entry = result.get("demo.extension")
    assert entry.activation_status == "blocked"
    assert entry.missing_grants == ("process",)
    assert any(item.code == "WORKSPACE_EXTENSION_MANIFEST_CHANGED" for item in entry.diagnostics)
    assert state.get("demo.extension") == selection


def test_complete_grants_are_ready_and_excess_grants_are_warnings() -> None:
    selection = WorkspaceExtensionSelection("demo.extension", ("read", "old"), True)
    state = WorkspaceExtensionsState((selection,))
    result = resolve_workspace_extensions(
        state,
        ExtensionCatalogDocument(),
        (_observation("demo.extension", "unchanged", ("read",)),),
    )
    entry = result.get("demo.extension")
    assert entry.activation_status == "blocked"  # orphan takes precedence over grants
    assert entry.effective_grants == ("read",)
    assert entry.unused_grants == ("old",)


def test_ready_requires_catalog_and_unchanged_manifest() -> None:
    selection = WorkspaceExtensionSelection("demo.extension", ("read",), True)
    state = WorkspaceExtensionsState((selection,))
    # A catalog entry is not needed to test the pure shape; orphan remains blocked.
    result = resolve_workspace_extensions(
        state,
        ExtensionCatalogDocument(),
        (_observation("demo.extension", "unchanged", ("read",)),),
    )
    assert result.get("demo.extension").activation_status == "blocked"


@pytest.mark.parametrize("enabled", [True, False])
def test_present_catalog_without_observation_is_explicitly_unavailable(enabled: bool) -> None:
    entry = PersistedCatalogEntry(
        "demo.extension",
        PersistedManifestPath("/tmp/demo-manifest.json", "posix"),
        "f" * 64,
    )
    state = WorkspaceExtensionsState((WorkspaceExtensionSelection("demo.extension", ("old",), enabled),))
    result = resolve_workspace_extensions(state, ExtensionCatalogDocument((entry,)), ())
    resolved = result.get("demo.extension")
    assert resolved.activation_status == ("blocked" if enabled else "disabled")
    assert resolved.required_capabilities == ()
    assert resolved.missing_grants == ()
    assert resolved.unused_grants == ()
    assert [item.code for item in resolved.diagnostics].count(
        "WORKSPACE_EXTENSION_MANIFEST_UNAVAILABLE"
    ) == 1
