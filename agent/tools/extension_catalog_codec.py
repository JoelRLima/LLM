"""Strict deterministic codec for the persisted extension catalog."""

from __future__ import annotations

import json
from typing import Any

from agent.tools.extension_catalog_document import (
    ExtensionCatalogDocument,
    PersistedCatalogEntry,
)
from agent.tools.extension_catalog_errors import (
    CatalogCodecError,
    CatalogSchemaError,
    CatalogVersionError,
)
from agent.tools.extension_path import PersistedManifestPath
from agent.tools.extension_state import (
    CATALOG_SCHEMA_VERSION,
    validate_extension_id,
    validate_manifest_fingerprint,
    validate_schema_version,
)

_ROOT_FIELDS = frozenset(("schema_version", "extensions"))
_ENTRY_FIELDS = frozenset(("manifest_path", "manifest_path_flavor", "manifest_sha256"))


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CatalogSchemaError(f"Chave JSON duplicada: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise CatalogSchemaError(f"Constante JSON não suportada: {value}")


def _require_exact_fields(payload: dict[str, Any], required: frozenset[str], context: str) -> None:
    missing = sorted(required - set(payload))
    unknown = sorted(set(payload) - required)
    if missing:
        raise CatalogSchemaError(f"Campos ausentes em {context}: {', '.join(missing)}")
    if unknown:
        raise CatalogSchemaError(f"Campos desconhecidos em {context}: {', '.join(unknown)}")


def _decode_json(data: bytes) -> object:
    if not isinstance(data, bytes):
        raise TypeError("catalog bytes deve ser bytes")
    if not data:
        raise CatalogCodecError("Catálogo vazio")
    if data.startswith(b"\xef\xbb\xbf"):
        raise CatalogCodecError("BOM UTF-8 não é aceito no catálogo")
    try:
        text = data.decode("utf-8")
        return json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except CatalogCodecError:
        raise
    except UnicodeDecodeError as exc:
        raise CatalogCodecError("Catálogo não está codificado em UTF-8") from exc
    except (json.JSONDecodeError, TypeError) as exc:
        raise CatalogCodecError("JSON de catálogo inválido") from exc


def _decode_entry(extension_id: str, raw_entry: object) -> PersistedCatalogEntry:
    if not isinstance(raw_entry, dict):
        raise CatalogSchemaError(f"Entrada inválida para {extension_id}")
    _require_exact_fields(raw_entry, _ENTRY_FIELDS, f"extension {extension_id}")
    try:
        path = PersistedManifestPath(raw_entry["manifest_path"], raw_entry["manifest_path_flavor"])
        fingerprint = raw_entry["manifest_sha256"]
        validate_manifest_fingerprint(fingerprint)
        return PersistedCatalogEntry(extension_id, path, fingerprint)
    except (TypeError, ValueError) as exc:
        raise CatalogSchemaError(f"Entrada inválida para {extension_id}: {exc}") from exc


def _decode_entries(extensions: dict[str, Any]) -> ExtensionCatalogDocument:
    entries: list[PersistedCatalogEntry] = []
    for extension_id, raw_entry in extensions.items():
        if not isinstance(extension_id, str):
            raise CatalogSchemaError("Chave de extension deve ser string")
        try:
            validate_extension_id(extension_id)
        except ValueError as exc:
            raise CatalogSchemaError(str(exc)) from exc
        entries.append(_decode_entry(extension_id, raw_entry))
    try:
        return ExtensionCatalogDocument(tuple(entries))
    except (TypeError, ValueError) as exc:
        raise CatalogSchemaError(str(exc)) from exc


def decode_catalog(data: bytes) -> ExtensionCatalogDocument:
    """Decode strict UTF-8 bytes into a validated immutable document."""

    payload = _decode_json(data)
    if not isinstance(payload, dict):
        raise CatalogSchemaError("Raiz do catálogo deve ser um objeto")
    _require_exact_fields(payload, _ROOT_FIELDS, "catálogo")
    try:
        validate_schema_version(payload["schema_version"], CATALOG_SCHEMA_VERSION)
    except ValueError as exc:
        raise CatalogVersionError(str(exc)) from exc
    extensions = payload["extensions"]
    if not isinstance(extensions, dict):
        raise CatalogSchemaError("extensions deve ser um objeto")
    return _decode_entries(extensions)


def encode_catalog(document: ExtensionCatalogDocument) -> bytes:
    """Encode a validated document deterministically as UTF-8 JSON bytes."""

    if not isinstance(document, ExtensionCatalogDocument):
        raise TypeError("document deve ser ExtensionCatalogDocument")
    payload = {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "extensions": {
            entry.extension_id: {
                "manifest_path": entry.manifest_path.persisted_value,
                "manifest_path_flavor": entry.manifest_path.flavor,
                "manifest_sha256": entry.manifest_sha256,
            }
            for entry in document.entries
        },
    }
    try:
        return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        )
    except (TypeError, ValueError) as exc:
        raise CatalogCodecError("Documento de catálogo não pôde ser codificado") from exc


__all__ = ["decode_catalog", "encode_catalog"]
