import pytest

from agent.tools.extension_state import (
    CATALOG_SCHEMA_VERSION,
    WORKSPACE_SCHEMA_VERSION,
    ExtensionCatalog,
    ExtensionCatalogEntry,
    WorkspaceExtensionSelection,
    WorkspaceExtensionsState,
    fingerprint_for_bytes,
    validate_extension_id,
    validate_manifest_fingerprint,
    validate_schema_version,
)


def _entry(
    extension_id: str = "demo.extension",
    path: str = "demo/manifest.json",
    fingerprint: str | None = None,
) -> ExtensionCatalogEntry:
    return ExtensionCatalogEntry(
        extension_id=extension_id,
        manifest_path=path,
        manifest_sha256=fingerprint or ("a" * 64),
    )


@pytest.mark.parametrize("extension_id", ["demo.extension", "security.skill_scanner", "vendor-extension"])
def test_extension_id_accepts_canonical_forms(extension_id: str) -> None:
    assert validate_extension_id(extension_id) == extension_id


@pytest.mark.parametrize(
    "extension_id",
    [
        "",
        "demo extension",
        "Demo.extension",
        "demo/extension",
        ".demo",
        "demo-",
        "éxtension",
        "demo..extension",
        "demo__extension",
        "demo--extension",
        "demo.-extension",
        "demo_.extension",
    ],
)
def test_extension_id_rejects_invalid_forms(extension_id: str) -> None:
    with pytest.raises(ValueError):
        validate_extension_id(extension_id)


def test_fingerprint_accepts_lowercase_sha256() -> None:
    fingerprint = "0123456789abcdef" * 4
    payload = b"abc"
    expected = (
        "ba7816bf8f01cfea414140de5dae2223"
        "b00361a396177a9cb410ff61f20015ad"
    )

    assert validate_manifest_fingerprint(fingerprint) == fingerprint
    assert fingerprint_for_bytes(payload) == expected
    assert validate_manifest_fingerprint(expected) == expected


@pytest.mark.parametrize("fingerprint", ["a" * 63, "a" * 65, "g" * 64, ("A" + "a" * 63)])
def test_fingerprint_rejects_invalid_shape(fingerprint: str) -> None:
    with pytest.raises(ValueError):
        validate_manifest_fingerprint(fingerprint)


def test_catalog_entry_and_catalog_are_validated_and_sorted() -> None:
    catalog = ExtensionCatalog().register(_entry("z.extension", "z.json")).register(_entry())

    assert catalog.entries == (_entry(), _entry("z.extension", "z.json"))
    assert catalog.get("demo.extension") == _entry()


def test_same_catalog_entry_is_idempotent() -> None:
    catalog = ExtensionCatalog().register(_entry())

    assert catalog.register(_entry()) is catalog


@pytest.mark.parametrize(
    "entry",
    [
        _entry(path="other/manifest.json"),
        _entry(fingerprint="b" * 64),
    ],
)
def test_same_id_with_different_origin_or_fingerprint_is_rejected(
    entry: ExtensionCatalogEntry,
) -> None:
    catalog = ExtensionCatalog().register(_entry())

    with pytest.raises(ValueError):
        catalog.register(entry)


def test_same_path_under_different_id_is_rejected() -> None:
    catalog = ExtensionCatalog().register(_entry())

    with pytest.raises(ValueError):
        catalog.register(_entry("other.extension"))


def test_catalog_constructor_rejects_duplicate_ids_and_paths() -> None:
    with pytest.raises(ValueError):
        ExtensionCatalog(
            (
                _entry("first.extension", "first.json"),
                _entry("first.extension", "second.json", "b" * 64),
            )
        )

    with pytest.raises(ValueError):
        ExtensionCatalog(
            (
                _entry("first.extension", "shared.json"),
                _entry("second.extension", "shared.json", "b" * 64),
            )
        )


def test_workspace_constructor_rejects_duplicate_selections() -> None:
    with pytest.raises(ValueError):
        WorkspaceExtensionsState(
            (
                WorkspaceExtensionSelection("demo.extension"),
                WorkspaceExtensionSelection("demo.extension", ("read",)),
            )
        )


def test_catalog_copies_mutable_source_collection() -> None:
    source = [_entry()]
    catalog = ExtensionCatalog(source)

    source.append(_entry("other.extension", "other.json"))

    assert catalog.entries == (_entry(),)


def test_workspace_selection_copies_mutable_grants_collection() -> None:
    grants = ["write", "read"]
    selection = WorkspaceExtensionSelection("demo.extension", grants)

    grants.append("network")

    assert selection.granted_capabilities == ("read", "write")


def test_different_ids_and_paths_are_allowed() -> None:
    catalog = ExtensionCatalog().register(_entry())

    result = catalog.register(_entry("other.extension", "other/manifest.json"))

    assert tuple(entry.extension_id for entry in result.entries) == (
        "demo.extension",
        "other.extension",
    )


def test_workspace_enablement_is_local_and_grants_are_distinct() -> None:
    selection = WorkspaceExtensionSelection("demo.extension", ("write", "read"))
    workspace_a = WorkspaceExtensionsState().enable(selection)
    workspace_b = WorkspaceExtensionsState()

    assert workspace_a.is_enabled("demo.extension")
    assert workspace_a.get("demo.extension").granted_capabilities == ("read", "write")
    assert not workspace_b.is_enabled("demo.extension")


def test_workspace_grants_reject_empty_and_duplicate_values() -> None:
    with pytest.raises(ValueError):
        WorkspaceExtensionSelection("demo.extension", ("",))
    with pytest.raises(ValueError):
        WorkspaceExtensionSelection("demo.extension", ("read", "read"))


def test_empty_grant_collection_represents_enabled_without_authority() -> None:
    selection = WorkspaceExtensionSelection("demo.extension")

    assert selection.granted_capabilities == ()


def test_workspace_enablement_is_idempotent_and_replacement_is_rejected() -> None:
    selection = WorkspaceExtensionSelection("demo.extension", ("read",))
    state = WorkspaceExtensionsState().enable(selection)

    assert state.enable(selection) is state
    with pytest.raises(ValueError):
        state.enable(WorkspaceExtensionSelection("demo.extension", ("write",)))


def test_orphaned_workspace_reference_is_preserved_and_reported() -> None:
    state = WorkspaceExtensionsState().enable(WorkspaceExtensionSelection("missing.extension"))

    assert state.enabled_ids() == ("missing.extension",)
    assert state.orphaned_ids(ExtensionCatalog()) == ("missing.extension",)


def test_snapshots_do_not_share_mutable_state_and_capabilities_are_deterministic() -> None:
    original = WorkspaceExtensionsState()
    updated = original.enable(WorkspaceExtensionSelection("demo.extension", ("write", "read")))

    assert original.selections == ()
    assert updated.enabled_ids() == ("demo.extension",)
    assert updated.get("demo.extension").granted_capabilities == ("read", "write")


def test_schema_versions_are_explicit_and_strict() -> None:
    assert validate_schema_version(CATALOG_SCHEMA_VERSION, CATALOG_SCHEMA_VERSION) == 1
    assert validate_schema_version(WORKSPACE_SCHEMA_VERSION, WORKSPACE_SCHEMA_VERSION) == 1
    with pytest.raises(ValueError):
        validate_schema_version(2, CATALOG_SCHEMA_VERSION)
    with pytest.raises(ValueError):
        validate_schema_version(True, CATALOG_SCHEMA_VERSION)
