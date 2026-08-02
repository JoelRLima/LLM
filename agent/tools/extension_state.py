"""Immutable models for extension catalog and workspace enablement state.

This module deliberately contains no filesystem or runtime integration.  The
catalog and workspace state are snapshots that later persistence and bootstrap
layers can consume without sharing mutable administrative state.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

CATALOG_SCHEMA_VERSION = 1
WORKSPACE_SCHEMA_VERSION = 1
_EXTENSION_ID_PATTERN = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*\Z")
_FINGERPRINT_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


def validate_extension_id(extension_id: str) -> str:
    """Validate the canonical, case-sensitive extension identifier."""

    if not isinstance(extension_id, str) or _EXTENSION_ID_PATTERN.fullmatch(extension_id) is None:
        raise ValueError(
            "extension_id deve conter apenas ASCII minúsculo, números, '.', '_' ou '-' "
            "e começar/terminar com um caractere alfanumérico"
        )
    return extension_id


def validate_manifest_fingerprint(fingerprint: str) -> str:
    """Validate a lowercase SHA-256 hexadecimal fingerprint."""

    if not isinstance(fingerprint, str) or _FINGERPRINT_PATTERN.fullmatch(fingerprint) is None:
        raise ValueError("manifest_sha256 deve ser SHA-256 hexadecimal minúsculo")
    return fingerprint


def fingerprint_for_bytes(content: bytes) -> str:
    """Return the SHA-256 fingerprint of the exact manifest bytes supplied."""

    if not isinstance(content, bytes):
        raise TypeError("content deve ser bytes")
    return hashlib.sha256(content).hexdigest()


def validate_schema_version(value: object, expected: int) -> int:
    """Validate a future persisted-document schema version without doing I/O."""

    if type(value) is not int or value != expected:  # bool must not act as an int.
        raise ValueError(f"schema_version incompatível: esperado {expected}")
    return value


@dataclass(frozen=True)
class ExtensionCatalogEntry:
    """A validated reference to an extension known by the global catalog."""

    extension_id: str
    manifest_path: Path
    manifest_sha256: str

    def __post_init__(self) -> None:
        validate_extension_id(self.extension_id)
        if isinstance(self.manifest_path, str):
            if not self.manifest_path.strip():
                raise ValueError("manifest_path não pode ser vazio")
            path = Path(self.manifest_path)
        elif isinstance(self.manifest_path, Path):
            path = self.manifest_path
        else:
            raise TypeError("manifest_path deve ser str ou Path")
        validate_manifest_fingerprint(self.manifest_sha256)
        object.__setattr__(self, "manifest_path", path)


@dataclass(frozen=True)
class ExtensionCatalog:
    """Immutable global catalog snapshot.

    Registering an entry returns a new snapshot.  The model does not check the
    path or read the manifest; those checks belong to the persistence/validation
    layer of the next stage.
    """

    entries: tuple[ExtensionCatalogEntry, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        entries = tuple(self.entries)
        if any(not isinstance(entry, ExtensionCatalogEntry) for entry in entries):
            raise TypeError("entries deve conter ExtensionCatalogEntry")
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

    def get(self, extension_id: str) -> ExtensionCatalogEntry | None:
        """Return an entry by ID without normalizing the requested value."""

        return next((entry for entry in self.entries if entry.extension_id == extension_id), None)

    def register(self, entry: ExtensionCatalogEntry) -> "ExtensionCatalog":
        """Add an entry, preserving idempotency and rejecting replacement."""

        existing = self.get(entry.extension_id)
        if existing is not None:
            if (
                existing.manifest_path == entry.manifest_path
                and existing.manifest_sha256 == entry.manifest_sha256
            ):
                return self
            raise ValueError(f"extension_id já registrado com outra origem: {entry.extension_id}")
        if any(item.manifest_path == entry.manifest_path for item in self.entries):
            raise ValueError("manifest_path já registrado sob outro extension_id")
        return ExtensionCatalog(self.entries + (entry,))


def _normalize_capabilities(capabilities: Iterable[str]) -> tuple[str, ...]:
    if isinstance(capabilities, str):
        raise TypeError("granted_capabilities deve ser uma coleção de strings")
    try:
        values = tuple(capabilities)
    except TypeError as exc:
        raise TypeError("granted_capabilities deve ser iterável") from exc
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError("granted_capabilities não pode conter valores vazios")
    if len(values) != len(set(values)):
        raise ValueError("granted_capabilities contém duplicata")
    return tuple(sorted(values))


@dataclass(frozen=True)
class WorkspaceExtensionSelection:
    """One workspace-local enablement and its explicitly granted capabilities."""

    extension_id: str
    granted_capabilities: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        validate_extension_id(self.extension_id)
        object.__setattr__(
            self,
            "granted_capabilities",
            _normalize_capabilities(self.granted_capabilities),
        )


@dataclass(frozen=True)
class WorkspaceExtensionsState:
    """Immutable workspace snapshot; unknown IDs remain representable as orphans."""

    selections: tuple[WorkspaceExtensionSelection, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        selections = tuple(self.selections)
        if any(not isinstance(selection, WorkspaceExtensionSelection) for selection in selections):
            raise TypeError("selections deve conter WorkspaceExtensionSelection")
        ids = [selection.extension_id for selection in selections]
        if len(ids) != len(set(ids)):
            raise ValueError("workspace contém extension_id duplicado")
        object.__setattr__(
            self,
            "selections",
            tuple(sorted(selections, key=lambda selection: selection.extension_id)),
        )

    def get(self, extension_id: str) -> WorkspaceExtensionSelection | None:
        return next(
            (selection for selection in self.selections if selection.extension_id == extension_id),
            None,
        )

    def enable(self, selection: WorkspaceExtensionSelection) -> "WorkspaceExtensionsState":
        """Return a new state, preserving idempotency and rejecting replacement."""

        existing = self.get(selection.extension_id)
        if existing is not None:
            if existing == selection:
                return self
            raise ValueError(
                f"extension_id já habilitado com grants diferentes: {selection.extension_id}"
            )
        return WorkspaceExtensionsState(self.selections + (selection,))

    def is_enabled(self, extension_id: str) -> bool:
        return self.get(extension_id) is not None

    def enabled_ids(self) -> tuple[str, ...]:
        return tuple(selection.extension_id for selection in self.selections)

    def orphaned_ids(self, catalog: ExtensionCatalog) -> tuple[str, ...]:
        """Return enabled IDs absent from the supplied catalog without removing them."""

        return tuple(
            extension_id
            for extension_id in self.enabled_ids()
            if catalog.get(extension_id) is None
        )


__all__ = [
    "CATALOG_SCHEMA_VERSION",
    "WORKSPACE_SCHEMA_VERSION",
    "ExtensionCatalog",
    "ExtensionCatalogEntry",
    "WorkspaceExtensionSelection",
    "WorkspaceExtensionsState",
    "fingerprint_for_bytes",
    "validate_extension_id",
    "validate_manifest_fingerprint",
    "validate_schema_version",
]
