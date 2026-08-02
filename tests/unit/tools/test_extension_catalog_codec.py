import json

import pytest

from agent.tools.extension_catalog_codec import decode_catalog, encode_catalog
from agent.tools.extension_catalog_document import (
    ExtensionCatalogDocument,
    PersistedCatalogEntry,
)
from agent.tools.extension_catalog_errors import CatalogCodecError, CatalogSchemaError, CatalogVersionError
from agent.tools.extension_path import PersistedManifestPath

FINGERPRINT = "0123456789abcdef" * 4


def _entry(
    extension_id: str = "demo.extension",
    path: str = "/opt/extensions/demo/manifest.json",
    flavor: str = "posix",
    fingerprint: str = FINGERPRINT,
) -> PersistedCatalogEntry:
    return PersistedCatalogEntry(
        extension_id,
        PersistedManifestPath(path, flavor),
        fingerprint,
    )


def _payload(*entries: tuple[str, dict[str, object]]) -> bytes:
    return json.dumps(
        {"schema_version": 1, "extensions": dict(entries)},
        ensure_ascii=False,
    ).encode("utf-8")


def test_empty_document_round_trips_deterministically() -> None:
    document = ExtensionCatalogDocument()

    encoded = encode_catalog(document)

    assert encoded.endswith(b"\n")
    assert encoded == b'{\n  "extensions": {},\n  "schema_version": 1\n}\n'
    assert decode_catalog(encoded) == document


def test_unicode_and_sorting_are_deterministic() -> None:
    document = ExtensionCatalogDocument(
        (_entry("z.extension"), _entry("unicode.extension", "/opt/á/manifest.json"))
    )

    encoded = encode_catalog(document)

    assert "á".encode("utf-8") in encoded
    assert b"\\u00e1" not in encoded
    assert encoded == encode_catalog(document)
    assert decode_catalog(encoded) == document


def test_decode_valid_document() -> None:
    data = _payload(
        (
            "demo.extension",
            {
                "manifest_path": "/opt/extensions/demo/manifest.json",
                "manifest_path_flavor": "posix",
                "manifest_sha256": FINGERPRINT,
            },
        )
    )

    document = decode_catalog(data)

    assert document.get("demo.extension").manifest_path.persisted_value == "/opt/extensions/demo/manifest.json"


@pytest.mark.parametrize("data", [b"", b"not json", b"[]", b"null", b"\xef\xbb\xbf{}"])
def test_decode_rejects_empty_invalid_root_and_bom(data: bytes) -> None:
    with pytest.raises(CatalogCodecError):
        decode_catalog(data)


def test_decode_rejects_invalid_utf8() -> None:
    with pytest.raises(CatalogCodecError, match="UTF-8"):
        decode_catalog(b"\xff")


@pytest.mark.parametrize(
    "data",
    [
        b'{"extensions": {}}',
        b'{"schema_version": 1}',
        b'{"schema_version": true, "extensions": {}}',
        b'{"schema_version": 2, "extensions": {}}',
        b'{"schema_version": 1, "extensions": [], "extra": 1}',
    ],
)
def test_decode_rejects_root_schema_errors(data: bytes) -> None:
    with pytest.raises(CatalogSchemaError):
        decode_catalog(data)


def test_decode_uses_specific_version_error() -> None:
    with pytest.raises(CatalogVersionError):
        decode_catalog(b'{"schema_version":2,"extensions":{}}')


def test_decode_rejects_duplicate_keys_at_root_and_entry() -> None:
    root_duplicate = b'{"schema_version": 1, "schema_version": 1, "extensions": {}}'
    entry_duplicate = (
        b'{"schema_version":1,"extensions":{"demo.extension":'
        b'{"manifest_path":"/opt/a.json","manifest_path":"/opt/b.json",'
        b'"manifest_path_flavor":"posix","manifest_sha256":"' + FINGERPRINT.encode() + b'"}}}'
    )

    with pytest.raises(CatalogSchemaError, match="duplicada"):
        decode_catalog(root_duplicate)
    with pytest.raises(CatalogSchemaError, match="duplicada"):
        decode_catalog(entry_duplicate)


@pytest.mark.parametrize(
    "raw_entry",
    [
        {},
        {"manifest_path": "/opt/a.json", "manifest_path_flavor": "posix"},
        {
            "manifest_path": "/opt/a.json",
            "manifest_path_flavor": "posix",
            "manifest_sha256": FINGERPRINT,
            "extra": 1,
        },
        {
            "manifest_path": "relative.json",
            "manifest_path_flavor": "posix",
            "manifest_sha256": FINGERPRINT,
        },
        {
            "manifest_path": "/opt/a.json",
            "manifest_path_flavor": "macos",
            "manifest_sha256": FINGERPRINT,
        },
        {
            "manifest_path": "/opt/a.json",
            "manifest_path_flavor": "posix",
            "manifest_sha256": "bad",
        },
    ],
)
def test_decode_rejects_invalid_entries(raw_entry: dict[str, object]) -> None:
    with pytest.raises(CatalogSchemaError):
        decode_catalog(_payload(("demo.extension", raw_entry)))


def test_decode_rejects_invalid_id_and_duplicate_semantic_path() -> None:
    invalid_id = _payload(("Bad ID", {
        "manifest_path": "/opt/a.json",
        "manifest_path_flavor": "posix",
        "manifest_sha256": FINGERPRINT,
    }))
    duplicate_path = json.dumps(
        {
            "schema_version": 1,
            "extensions": {
                "first.extension": {
                    "manifest_path": "C:/Extensions/demo.json",
                    "manifest_path_flavor": "windows",
                    "manifest_sha256": FINGERPRINT,
                },
                "second.extension": {
                    "manifest_path": "c:/extensions/demo.json",
                    "manifest_path_flavor": "windows",
                    "manifest_sha256": "a" * 64,
                },
            },
        }
    ).encode()

    with pytest.raises(CatalogSchemaError):
        decode_catalog(invalid_id)
    with pytest.raises(CatalogSchemaError, match="manifest_path"):
        decode_catalog(duplicate_path)
