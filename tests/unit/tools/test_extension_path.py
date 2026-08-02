from dataclasses import FrozenInstanceError, replace
from typing import cast

import pytest

from agent.tools.extension_path import (
    SUPPORTED_PATH_FLAVORS,
    PathFlavor,
    PersistedManifestPath,
)
from agent.tools.extension_state import ExtensionCatalog, ExtensionCatalogEntry


@pytest.mark.parametrize("flavor", ["windows", "posix"])
def test_supported_flavors_are_accepted(flavor: str) -> None:
    value = (
        "C:/extensions/demo/manifest.json"
        if flavor == "windows"
        else "/opt/extensions/demo/manifest.json"
    )

    path = PersistedManifestPath(value, cast(PathFlavor, flavor))

    assert path.flavor == flavor
    assert path.persisted_value == value


@pytest.mark.parametrize("flavor", ["", "macos", "WINDOWS", None, True])
def test_unknown_or_non_string_flavor_is_rejected(flavor: object) -> None:
    with pytest.raises(ValueError):
        PersistedManifestPath(
            "/opt/extensions/demo/manifest.json",
            cast(PathFlavor, flavor),
        )


def test_supported_flavors_are_explicit() -> None:
    assert SUPPORTED_PATH_FLAVORS == frozenset({"windows", "posix"})


@pytest.mark.parametrize(
    "value",
    [
        "C:/Users/user/extensions/demo/manifest.json",
        "D:/extensions/demo/manifest.json",
        "//server/share/extensions/demo/manifest.json",
        "C:/",
        "//server/share",
    ],
)
def test_windows_absolute_and_unc_paths_are_accepted(value: str) -> None:
    path = PersistedManifestPath(value, "windows")

    assert path.persisted_value == value
    assert path.is_compatible_with("windows")
    assert not path.is_compatible_with("posix")


@pytest.mark.parametrize(
    "value",
    [
        "",
        "C:\\Users\\user\\extensions\\demo\\manifest.json",
        "C:extensions/demo/manifest.json",
        "extensions/demo/manifest.json",
        "C:/extensions/./demo/manifest.json",
        "C:/extensions/../demo/manifest.json",
        "C:/extensions//demo/manifest.json",
        "C:/extensions/demo/manifest.json/",
        "//server/share/",
        "//server//share/demo/manifest.json",
        "/opt/extensions/demo/manifest.json",
    ],
)
def test_invalid_windows_paths_are_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        PersistedManifestPath(value, "windows")


@pytest.mark.parametrize(
    "value",
    [
        "/home/user/extensions/demo/manifest.json",
        "/opt/extensions/demo/manifest.json",
        "/",
        "/home/usuário/extensão/manifest.json",
    ],
)
def test_posix_absolute_paths_are_accepted(value: str) -> None:
    path = PersistedManifestPath(value, "posix")

    assert path.persisted_value == value
    assert path.is_compatible_with("posix")
    assert not path.is_compatible_with("windows")


@pytest.mark.parametrize(
    "value",
    [
        "",
        "home/user/extensions/demo/manifest.json",
        "./extensions/demo/manifest.json",
        "/extensions/../demo/manifest.json",
        "/extensions/./demo/manifest.json",
        "/extensions//demo/manifest.json",
        "/extensions/demo/manifest.json/",
        "//server/share/demo/manifest.json",
        "C:/extensions/demo/manifest.json",
    ],
)
def test_invalid_posix_paths_are_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        PersistedManifestPath(value, "posix")


def test_windows_comparison_is_case_insensitive_and_preserves_text() -> None:
    upper = PersistedManifestPath(
        "C:/Users/Alice/Extension/manifest.json", "windows"
    )
    lower = PersistedManifestPath(
        "c:/users/alice/extension/manifest.json", "windows"
    )

    assert upper.persisted_value != lower.persisted_value
    assert upper.comparison_key == lower.comparison_key
    assert upper.equivalent_to(lower)


def test_posix_comparison_is_case_sensitive() -> None:
    upper = PersistedManifestPath("/home/Alice/extension/manifest.json", "posix")
    lower = PersistedManifestPath("/home/alice/extension/manifest.json", "posix")

    assert upper.comparison_key != lower.comparison_key
    assert not upper.equivalent_to(lower)


def test_windows_identity_is_shared_by_equality_hash_set_and_dict() -> None:
    upper = PersistedManifestPath("C:/Users/Alice/manifest.json", "windows")
    lower = PersistedManifestPath("c:/users/alice/manifest.json", "windows")

    assert upper == lower
    assert upper.equivalent_to(lower)
    assert hash(upper) == hash(lower)
    assert len({upper, lower}) == 1
    assert len({upper: 1, lower: 2}) == 1


def test_posix_case_difference_remains_distinct_in_collections() -> None:
    upper = PersistedManifestPath("/home/Alice/manifest.json", "posix")
    lower = PersistedManifestPath("/home/alice/manifest.json", "posix")

    assert upper != lower
    assert not upper.equivalent_to(lower)
    assert len({upper, lower}) == 2


def test_comparison_with_other_type_is_safe_and_returns_not_implemented() -> None:
    path = PersistedManifestPath("/opt/extensions/demo/manifest.json", "posix")

    assert path.__eq__(object()) is NotImplemented
    assert (path == object()) is False
    assert path.equivalent_to(object()) is False


class _MaliciousFlavor:
    def __eq__(self, other: object) -> bool:
        return other == "windows"


class _StringFlavor(str):
    pass


@pytest.mark.parametrize(
    "flavor",
    [_MaliciousFlavor(), False, True, 1, None, _StringFlavor("windows")],
)
def test_flavor_requires_an_exact_supported_string(flavor: object) -> None:
    with pytest.raises(ValueError):
        PersistedManifestPath("C:/extensions/demo/manifest.json", cast(PathFlavor, flavor))


def test_windows_unicode_case_does_not_introduce_casefold_collisions() -> None:
    sharp_s = PersistedManifestPath("C:/Straße/manifest.json", "windows")
    ss = PersistedManifestPath("C:/Strasse/manifest.json", "windows")

    assert sharp_s.comparison_key != ss.comparison_key
    assert sharp_s != ss
    assert not sharp_s.equivalent_to(ss)
    assert sharp_s.persisted_value == "C:/Straße/manifest.json"


@pytest.mark.parametrize(
    "value",
    [
        "//?/UNC/server/share/manifest.json",
        "//?/C:/manifest.json",
        "//./server/share/manifest.json",
    ],
)
def test_windows_device_namespaces_are_rejected(value: str) -> None:
    with pytest.raises(ValueError, match="device namespace"):
        PersistedManifestPath(value, "windows")


def test_nul_is_rejected_and_replace_revalidates() -> None:
    with pytest.raises(ValueError):
        PersistedManifestPath("/opt/\x00/manifest.json", "posix")

    path = PersistedManifestPath("/opt/extensions/demo/manifest.json", "posix")
    with pytest.raises(ValueError):
        replace(path, value="../manifest.json")


def test_different_flavors_never_collide() -> None:
    windows = PersistedManifestPath("C:/extensions/demo/manifest.json", "windows")
    posix = PersistedManifestPath("/extensions/demo/manifest.json", "posix")

    assert windows.flavor != posix.flavor
    assert not windows.equivalent_to(posix)


def test_foreign_paths_are_representable_without_native_path_conversion() -> None:
    windows = PersistedManifestPath("C:/extensions/demo/manifest.json", "windows")
    posix = PersistedManifestPath("/opt/extensions/demo/manifest.json", "posix")

    assert windows.is_compatible_with("windows")
    assert not windows.is_compatible_with("posix")
    assert posix.is_compatible_with("posix")
    assert not posix.is_compatible_with("windows")


def test_paths_are_immutable_and_have_no_mutable_alias() -> None:
    path = PersistedManifestPath("/opt/extensions/demo/manifest.json", "posix")

    attribute = "value"
    with pytest.raises(FrozenInstanceError):
        setattr(path, attribute, "/other/manifest.json")

    assert isinstance(path.comparison_key, str)


def test_existing_catalog_entry_and_fingerprint_contract_remain_unchanged() -> None:
    entry = ExtensionCatalogEntry(
        extension_id="demo.extension",
        manifest_path="demo/manifest.json",
        manifest_sha256="a" * 64,
    )
    catalog = ExtensionCatalog().register(entry)

    assert catalog.get("demo.extension") == entry
    assert entry.manifest_sha256 == "a" * 64
