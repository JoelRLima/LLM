"""Immutable validated representation of the persisted extension catalog."""

from __future__ import annotations

from dataclasses import dataclass, field

from agent.tools.extension_path import PersistedManifestPath
from agent.tools.extension_state import (
    CATALOG_SCHEMA_VERSION,
    validate_extension_id,
    validate_manifest_fingerprint,
)


@dataclass(frozen=True)
class PersistedCatalogEntry:
    """One catalog entry using the canonical persisted path model."""

    extension_id: str
    manifest_path: PersistedManifestPath
    manifest_sha256: str

    def __post_init__(self) -> None:
        validate_extension_id(self.extension_id)
        if not isinstance(self.manifest_path, PersistedManifestPath):
            raise TypeError("manifest_path deve ser PersistedManifestPath")
        validate_manifest_fingerprint(self.manifest_sha256)


@dataclass(frozen=True)
class ExtensionCatalogDocument:
    """Immutable document model; persistence and operations live elsewhere."""

    entries: tuple[PersistedCatalogEntry, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        entries = tuple(self.entries)
        if any(not isinstance(entry, PersistedCatalogEntry) for entry in entries):
            raise TypeError("entries deve conter PersistedCatalogEntry")
        ids = [entry.extension_id for entry in entries]
        if len(ids) != len(set(ids)):
            raise ValueError("catálogo contém extension_id duplicado")
        paths = [entry.manifest_path for entry in entries]
        if len(paths) != len(set(paths)):
            raise ValueError("catálogo contém manifest_path duplicado")
        object.__setattr__(
            self,
            "entries",
            tuple(sorted(entries, key=lambda entry: entry.extension_id)),
        )

    def get(self, extension_id: str) -> PersistedCatalogEntry | None:
        """Return an entry by its canonical ID."""

        return next(
            (entry for entry in self.entries if entry.extension_id == extension_id),
            None,
        )


__all__ = [
    "CATALOG_SCHEMA_VERSION",
    "ExtensionCatalogDocument",
    "PersistedCatalogEntry",
]
