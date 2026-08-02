"""Administrative service for the global persisted extension catalog."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from agent.tools.extension_catalog_document import (
    ExtensionCatalogDocument,
    PersistedCatalogEntry,
)
from agent.tools.extension_catalog_errors import (
    CatalogCodecError,
    CatalogCorruptError,
    CatalogDiagnostic,
    CatalogDriftError,
    CatalogIdConflictError,
    CatalogManifestIncompatibleError,
    CatalogManifestInvalidError,
    CatalogManifestMissingError,
    CatalogPathConflictError,
    CatalogReplaceConflictError,
    CatalogStorageError,
)
from agent.tools.extension_catalog_lock import ExtensionCatalogLock
from agent.tools.extension_catalog_storage import ExtensionCatalogStorage
from agent.tools.extension_catalog_validation import validate_catalog_document
from agent.tools.extension_manifest_parser import (
    ManifestParseError,
    ManifestProtocolError,
    load_extension_manifest_bytes,
)
from agent.tools.extension_path import PathFlavor, PersistedManifestPath
from agent.tools.extension_state import (
    fingerprint_for_bytes,
    validate_extension_id,
    validate_manifest_fingerprint,
)


@dataclass(frozen=True)
class CatalogOperationResult:
    """Result of an idempotent catalog mutation."""

    document: ExtensionCatalogDocument
    changed: bool
    entry: PersistedCatalogEntry | None = None


@dataclass(frozen=True)
class CatalogInspection:
    """Catalog snapshot plus safe diagnostics for a corrupt load."""

    document: ExtensionCatalogDocument | None
    diagnostics: tuple[CatalogDiagnostic, ...] = ()


def host_path_flavor() -> PathFlavor:
    """Return the platform flavor used for operational manifest reads."""

    return "windows" if os.name == "nt" else "posix"


def canonicalize_manifest_path(
    path: str | Path,
    *,
    base_dir: str | Path | None = None,
    flavor: PathFlavor | None = None,
) -> tuple[Path, PersistedManifestPath]:
    """Resolve an administrative path without cwd, home, or symlink resolution."""

    raw = os.fspath(path)
    if not isinstance(raw, str) or not raw:
        raise CatalogManifestIncompatibleError("manifest path deve ser uma string não vazia")
    if raw.startswith("~"):
        raise CatalogManifestIncompatibleError("expansão implícita de '~' não é permitida")
    native_flavor = flavor or host_path_flavor()
    candidate = Path(raw)
    if not candidate.is_absolute():
        if base_dir is None:
            raise CatalogManifestIncompatibleError(
                "path relativo exige base_dir explícito"
            )
        base = Path(os.fspath(base_dir))
        if not base.is_absolute():
            raise CatalogManifestIncompatibleError("base_dir deve ser absoluto")
        candidate = base / candidate
    native = candidate.absolute()
    try:
        persisted = PersistedManifestPath(native.as_posix(), native_flavor)
    except (TypeError, ValueError) as exc:
        raise CatalogManifestIncompatibleError(str(exc)) from exc
    return native, persisted


class ExtensionCatalogService:
    """Coordinate validated manifest operations with storage and writer lock."""

    def __init__(
        self,
        storage: ExtensionCatalogStorage,
        *,
        lock: ExtensionCatalogLock | None = None,
        host_flavor_override: PathFlavor | None = None,
    ) -> None:
        self.storage = storage
        self.lock = lock or ExtensionCatalogLock(f"{storage.path}.lock")
        self.host_flavor_override = host_flavor_override

    @property
    def host_flavor(self) -> PathFlavor:
        return self.host_flavor_override or host_path_flavor()

    def load(self) -> ExtensionCatalogDocument:
        return self.storage.load()

    def inspect(self) -> CatalogInspection:
        try:
            return CatalogInspection(self.load())
        except (CatalogCodecError, CatalogStorageError) as exc:
            diagnostic = CatalogDiagnostic(
                extension_id=None,
                path=str(self.storage.path),
                flavor=None,
                state="invalid",
                code=type(exc).__name__,
                message=(
                    "Catálogo corrompido."
                    if isinstance(exc, CatalogCorruptError)
                    else "Não foi possível ler o catálogo."
                ),
            )
            return CatalogInspection(None, (diagnostic,))

    def prepare_entry(
        self,
        path: str | Path,
        *,
        base_dir: str | Path | None = None,
    ) -> PersistedCatalogEntry:
        native, persisted = canonicalize_manifest_path(
            path,
            base_dir=base_dir,
            flavor=self.host_flavor,
        )
        if not persisted.is_compatible_with(self.host_flavor):
            raise CatalogManifestIncompatibleError("manifest path é incompatível com o host")
        if not native.is_file():
            raise CatalogManifestMissingError(f"Manifest ausente: {persisted.persisted_value}")
        try:
            content = native.read_bytes()
        except OSError as exc:
            raise CatalogManifestMissingError(
                f"Manifest indisponível: {persisted.persisted_value}"
            ) from exc
        try:
            manifest = load_extension_manifest_bytes(content)
        except ManifestProtocolError as exc:
            raise CatalogManifestIncompatibleError(
                f"Protocolo de manifest incompatível: {persisted.persisted_value}"
            ) from exc
        except (ManifestParseError, TypeError, ValueError) as exc:
            raise CatalogManifestInvalidError(
                f"Manifest inválido: {persisted.persisted_value}"
            ) from exc
        try:
            extension_id = validate_extension_id(manifest.id)
        except ValueError as exc:
            raise CatalogManifestIncompatibleError(
                f"ID de manifest incompatível: {persisted.persisted_value}"
            ) from exc
        return PersistedCatalogEntry(
            extension_id=extension_id,
            manifest_path=persisted,
            manifest_sha256=fingerprint_for_bytes(content),
        )

    def add(
        self,
        path: str | Path,
        *,
        base_dir: str | Path | None = None,
    ) -> CatalogOperationResult:
        entry = self.prepare_entry(path, base_dir=base_dir)
        with self.lock:
            document = self.storage.load()
            current = document.get(entry.extension_id)
            if current is not None:
                if current == entry:
                    return CatalogOperationResult(document, False, entry)
                if current.manifest_path != entry.manifest_path:
                    raise CatalogIdConflictError(
                        f"extension_id já registrado com outro path: {entry.extension_id}"
                    )
                raise CatalogDriftError(
                    f"fingerprint divergente para extension_id: {entry.extension_id}"
                )
            if any(item.manifest_path == entry.manifest_path for item in document.entries):
                raise CatalogPathConflictError("manifest_path já registrado sob outro extension_id")
            updated = ExtensionCatalogDocument(document.entries + (entry,))
            self.storage.save(updated)
            return CatalogOperationResult(updated, True, entry)

    def remove(self, extension_id: str) -> CatalogOperationResult:
        validate_extension_id(extension_id)
        with self.lock:
            document = self.storage.load()
            current = document.get(extension_id)
            if current is None:
                return CatalogOperationResult(document, False)
            updated = ExtensionCatalogDocument(
                tuple(item for item in document.entries if item.extension_id != extension_id)
            )
            self.storage.save(updated)
            return CatalogOperationResult(updated, True, current)

    def replace(
        self,
        extension_id: str,
        path: str | Path,
        *,
        expected_fingerprint: str,
        base_dir: str | Path | None = None,
    ) -> CatalogOperationResult:
        validate_extension_id(extension_id)
        validate_manifest_fingerprint(expected_fingerprint)
        replacement = self.prepare_entry(path, base_dir=base_dir)
        if replacement.extension_id != extension_id:
            raise CatalogManifestIncompatibleError(
                "novo manifest.id não corresponde ao extension_id substituído"
            )
        with self.lock:
            document = self.storage.load()
            current = document.get(extension_id)
            if current is None:
                raise CatalogReplaceConflictError(f"extension_id não registrado: {extension_id}")
            if current == replacement:
                return CatalogOperationResult(document, False, replacement)
            if current.manifest_sha256 != expected_fingerprint:
                raise CatalogReplaceConflictError(
                    f"fingerprint esperado não corresponde a {extension_id}"
                )
            for item in document.entries:
                if item.extension_id != extension_id and item.manifest_path == replacement.manifest_path:
                    raise CatalogPathConflictError("manifest_path já registrado sob outro extension_id")
            updated = ExtensionCatalogDocument(
                tuple(replacement if item.extension_id == extension_id else item for item in document.entries)
            )
            self.storage.save(updated)
            return CatalogOperationResult(updated, True, replacement)

    def validate(self) -> tuple[CatalogDiagnostic, ...]:
        return validate_catalog_document(self.storage.load(), self.host_flavor)


__all__ = [
    "CatalogInspection",
    "CatalogOperationResult",
    "ExtensionCatalogService",
    "canonicalize_manifest_path",
    "host_path_flavor",
]
