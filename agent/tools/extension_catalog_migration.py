"""Explicit all-or-nothing migration from the legacy extension registry."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from agent.tools.extension_catalog_document import ExtensionCatalogDocument, PersistedCatalogEntry
from agent.tools.extension_catalog_errors import (
    CatalogDiagnostic,
    CatalogManifestIncompatibleError,
    CatalogManifestInvalidError,
    CatalogManifestMissingError,
    ExtensionCatalogError,
    LegacyMigrationError,
)
from agent.tools.extension_catalog_lock import ExtensionCatalogLock
from agent.tools.extension_catalog_service import ExtensionCatalogService
from agent.tools.extension_catalog_storage import ExtensionCatalogStorage
from agent.tools.extension_state import validate_extension_id

_LEGACY_ENTRY_FIELDS = frozenset(("manifest_path", "enabled"))


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise LegacyMigrationError(f"Chave JSON duplicada no registry legado: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise LegacyMigrationError(f"Constante JSON não suportada no registry legado: {value}")


def _read_legacy(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise LegacyMigrationError(f"Registry legado não encontrado: {path}")
    try:
        raw = path.read_bytes()
        if not raw or raw.startswith(b"\xef\xbb\xbf"):
            raise LegacyMigrationError("Registry legado vazio ou com BOM")
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except LegacyMigrationError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LegacyMigrationError("Registry legado inválido") from exc
    if not isinstance(payload, dict):
        raise LegacyMigrationError("Registry legado deve ser um objeto")
    return payload


def _explicit_file_path(value: str | Path, label: str) -> Path:
    raw = os.fspath(value)
    if not isinstance(raw, str) or not raw or raw.startswith("~"):
        raise LegacyMigrationError(f"{label} deve ser um path absoluto explícito")
    path = Path(raw)
    if not path.is_absolute():
        raise LegacyMigrationError(f"{label} deve ser um path absoluto explícito")
    return path


def _same_native_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.normpath(os.fspath(left))) == os.path.normcase(
        os.path.normpath(os.fspath(right))
    )


def _diagnostic(extension_id: str | None, error: BaseException) -> CatalogDiagnostic:
    if isinstance(error, CatalogManifestMissingError):
        code, message = "MANIFEST_MISSING", "manifest ausente ou indisponivel"
    elif isinstance(error, CatalogManifestIncompatibleError):
        code, message = "MANIFEST_INCOMPATIBLE", "manifest incompatível"
    elif isinstance(error, CatalogManifestInvalidError):
        code, message = "MANIFEST_INVALID", "manifest invalido"
    elif isinstance(error, ExtensionCatalogError):
        code, message = type(error).__name__, "entrada de catalogo invalida"
    else:
        code, message = type(error).__name__, "entrada de registry legado invalida"
    return CatalogDiagnostic(extension_id, None, None, "invalid", code, message)


def _build_entries(
    payload: dict[str, Any],
    service: ExtensionCatalogService,
    base_dir: str | Path | None,
) -> tuple[ExtensionCatalogDocument, tuple[CatalogDiagnostic, ...]]:
    entries: list[PersistedCatalogEntry] = []
    diagnostics: list[CatalogDiagnostic] = []
    for extension_id, raw_entry in sorted(payload.items()):
        try:
            entries.append(_build_entry(extension_id, raw_entry, service, base_dir))
        except (ExtensionCatalogError, TypeError, ValueError) as exc:
            diagnostics.append(_diagnostic(extension_id, exc))
    try:
        document = ExtensionCatalogDocument(tuple(entries))
    except (TypeError, ValueError) as exc:
        document = ExtensionCatalogDocument()
        diagnostics.append(_diagnostic(None, exc))
    return document, tuple(diagnostics)


def _build_entry(
    extension_id: str,
    raw_entry: object,
    service: ExtensionCatalogService,
    base_dir: str | Path | None,
) -> PersistedCatalogEntry:
    validate_extension_id(extension_id)
    if not isinstance(raw_entry, dict):
        raise ValueError("entrada deve ser objeto")
    unknown = sorted(set(raw_entry) - _LEGACY_ENTRY_FIELDS)
    if unknown:
        raise ValueError(f"campos desconhecidos: {', '.join(unknown)}")
    if "manifest_path" not in raw_entry:
        raise ValueError("manifest_path ausente")
    if "enabled" in raw_entry and type(raw_entry["enabled"]) is not bool:
        raise ValueError("enabled deve ser booleano")
    entry = service.prepare_entry(raw_entry["manifest_path"], base_dir=base_dir)
    if entry.extension_id != extension_id:
        raise ValueError("manifest.id não corresponde à chave do registry")
    return entry


def migrate_legacy(
    legacy_registry_path: str | Path,
    destination_catalog_path: str | Path,
    *,
    base_dir: str | Path | None = None,
    service: ExtensionCatalogService | None = None,
) -> ExtensionCatalogDocument:
    """Validate every legacy entry, then atomically promote one catalog."""

    source = _explicit_file_path(legacy_registry_path, "registry legado")
    destination = _explicit_file_path(destination_catalog_path, "catálogo de destino")
    if source == destination:
        raise LegacyMigrationError("Registry legado e catálogo de destino são iguais")
    target_service = service or ExtensionCatalogService(ExtensionCatalogStorage(destination))
    expected_lock_path = Path(f"{destination}.lock")
    if not _same_native_path(Path(target_service.storage.path), destination):
        raise LegacyMigrationError("Storage injetado não corresponde ao destino declarado")
    if not _same_native_path(Path(target_service.lock.path), expected_lock_path):
        raise LegacyMigrationError("Lock injetado não corresponde ao destino declarado")
    payload = _read_legacy(source)
    migrated, diagnostics = _build_entries(payload, target_service, base_dir)
    if diagnostics:
        raise LegacyMigrationError(
            "Migração legada rejeitada; nenhum destino foi alterado",
            tuple(diagnostics),
        )
    lock = target_service.lock or ExtensionCatalogLock(f"{destination}.lock")
    with lock:
        current = target_service.storage.load()
        if current.entries == migrated.entries:
            return current
        if target_service.storage.path.exists():
            raise LegacyMigrationError("Destino do catálogo já contém conteúdo diferente")
        target_service.storage.save(migrated)
        return migrated


__all__ = ["migrate_legacy"]
