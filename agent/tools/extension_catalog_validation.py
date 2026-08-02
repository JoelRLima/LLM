"""Manifest drift validation for persisted catalog entries."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from agent.tools.extension_catalog_document import ExtensionCatalogDocument, PersistedCatalogEntry
from agent.tools.extension_catalog_errors import CatalogDiagnostic
from agent.tools.extension_manifest_parser import (
    ManifestParseError,
    ManifestProtocolError,
    load_extension_manifest_bytes,
)
from agent.tools.extension_state import fingerprint_for_bytes

HostFlavor = Literal["windows", "posix"]


def validate_catalog_document(
    document: ExtensionCatalogDocument,
    host_flavor: HostFlavor,
) -> tuple[CatalogDiagnostic, ...]:
    return tuple(_validate_entry(entry, host_flavor) for entry in document.entries)


def _validate_entry(entry: PersistedCatalogEntry, host_flavor: HostFlavor) -> CatalogDiagnostic:
    path = entry.manifest_path
    if not path.is_compatible_with(host_flavor):
        return CatalogDiagnostic(
            entry.extension_id,
            path.persisted_value,
            path.flavor,
            "incompatible",
            "FOREIGN_FLAVOR",
            "manifest flavor incompatível com o host",
            entry.manifest_sha256,
        )
    native = Path(path.persisted_value)
    if not native.is_file():
        return CatalogDiagnostic(
            entry.extension_id,
            path.persisted_value,
            path.flavor,
            "missing",
            "MANIFEST_MISSING",
            "manifest ausente ou alvo de symlink indisponível",
            entry.manifest_sha256,
        )
    try:
        content = native.read_bytes()
        manifest = load_extension_manifest_bytes(content, mode="strict_catalog")
    except OSError:
        return CatalogDiagnostic(
            entry.extension_id,
            path.persisted_value,
            path.flavor,
            "missing",
            "MANIFEST_UNAVAILABLE",
            "manifest indisponível para leitura",
            entry.manifest_sha256,
        )
    except ManifestProtocolError:
        return CatalogDiagnostic(
            entry.extension_id,
            path.persisted_value,
            path.flavor,
            "incompatible",
            "MANIFEST_PROTOCOL_INCOMPATIBLE",
            "protocolo do manifest não é suportado",
            entry.manifest_sha256,
        )
    except (ManifestParseError, TypeError, ValueError):
        return CatalogDiagnostic(
            entry.extension_id,
            path.persisted_value,
            path.flavor,
            "invalid",
            "MANIFEST_INVALID",
            "manifest inválido",
            entry.manifest_sha256,
        )
    if manifest.id != entry.extension_id:
        return CatalogDiagnostic(
            entry.extension_id,
            path.persisted_value,
            path.flavor,
            "incompatible",
            "MANIFEST_ID_MISMATCH",
            "ID do manifest difere da extensão registrada",
            entry.manifest_sha256,
        )
    observed = fingerprint_for_bytes(content)
    if observed != entry.manifest_sha256:
        return CatalogDiagnostic(
            entry.extension_id,
            path.persisted_value,
            path.flavor,
            "changed",
            "MANIFEST_DRIFT",
            "fingerprint do manifest mudou",
            entry.manifest_sha256,
            observed,
        )
    return CatalogDiagnostic(
        entry.extension_id,
        path.persisted_value,
        path.flavor,
        "unchanged",
        "MANIFEST_UNCHANGED",
        "manifest permanece igual",
        entry.manifest_sha256,
        observed,
    )


__all__ = ["validate_catalog_document"]
