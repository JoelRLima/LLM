"""Typed errors and diagnostics for the persisted extension catalog."""

from __future__ import annotations

from dataclasses import dataclass


class ExtensionCatalogError(RuntimeError):
    """Base error for catalog operations."""


class WorkspaceExtensionError(ExtensionCatalogError):
    """Base error for workspace-local extension configuration."""


class WorkspaceCodecError(WorkspaceExtensionError):
    """Workspace configuration bytes are invalid."""


class WorkspaceSchemaError(WorkspaceCodecError):
    """Workspace configuration shape or field types are invalid."""


class WorkspaceVersionError(WorkspaceSchemaError):
    """Workspace configuration schema version is unsupported."""


class WorkspaceStorageError(WorkspaceExtensionError):
    """Workspace configuration could not be read or atomically written."""

    def __init__(
        self,
        message: str,
        *,
        secondary_errors: tuple[BaseException, ...] = (),
    ) -> None:
        super().__init__(message)
        self.secondary_errors = tuple(secondary_errors)

    def add_secondary_error(self, error: BaseException) -> None:
        self.secondary_errors = (*self.secondary_errors, error)


class WorkspaceConfigurationCorruptError(WorkspaceStorageError):
    """A present workspace configuration is unreadable or invalid."""


class WorkspaceExtensionNotConfiguredError(WorkspaceExtensionError):
    """An administrative grant operation needs an existing configuration."""


class WorkspaceExtensionMissingError(WorkspaceExtensionError):
    """An extension is not present in the global catalog."""


class WorkspaceManifestBlockedError(WorkspaceExtensionError):
    """The current manifest is missing, changed, invalid, or incompatible."""


class WorkspaceCapabilityInvalidError(WorkspaceExtensionError):
    """A capability identifier is syntactically invalid."""


class WorkspaceCapabilityNotDeclaredError(WorkspaceExtensionError):
    """A capability is not declared by the current manifest."""


class WorkspaceConfigurationConflictError(WorkspaceExtensionError):
    """A workspace configuration mutation conflicts with current state."""


class WorkspacePathError(WorkspaceExtensionError):
    """Workspace paths are relative, contradictory, or not canonical."""


class CatalogCodecError(ExtensionCatalogError):
    """The persisted document cannot be decoded or encoded."""


class CatalogSchemaError(CatalogCodecError):
    """The document shape or schema version is invalid."""


class CatalogVersionError(CatalogSchemaError):
    """The persisted catalog schema version is unsupported."""


class CatalogStorageError(ExtensionCatalogError):
    """The catalog file could not be read or atomically written.

    ``secondary_errors`` is a tuple so callers can inspect cleanup failures on
    Python versions that do not provide ``BaseException.add_note``.
    """

    def __init__(
        self,
        message: str,
        *,
        secondary_errors: tuple[BaseException, ...] = (),
    ) -> None:
        super().__init__(message)
        self.secondary_errors = tuple(secondary_errors)

    def add_secondary_error(self, error: BaseException) -> None:
        self.secondary_errors = (*self.secondary_errors, error)


class CatalogCorruptError(CatalogStorageError):
    """A present catalog file is unreadable or structurally invalid."""


class CatalogLockError(ExtensionCatalogError):
    """The catalog writer lock could not be acquired or released."""


class CatalogLockBusyError(CatalogLockError):
    """Another writer currently owns the catalog lock."""


class CatalogConflictError(ExtensionCatalogError):
    """An explicit catalog operation conflicts with current state."""


class CatalogIdConflictError(CatalogConflictError):
    """An extension ID is already associated with another path."""


class CatalogPathConflictError(CatalogConflictError):
    """A manifest path is already associated with another extension ID."""


class CatalogManifestError(ExtensionCatalogError):
    """A manifest cannot be used to create or update a catalog entry."""


class CatalogManifestMissingError(CatalogManifestError):
    """The manifest path is absent or its symlink target is unavailable."""


class CatalogManifestInvalidError(CatalogManifestError):
    """Manifest bytes are not valid UTF-8, JSON, or manifest structure."""


class CatalogManifestIncompatibleError(CatalogManifestError):
    """Manifest flavor, ID, or protocol is incompatible with the operation."""


class CatalogDriftError(CatalogConflictError):
    """An observed manifest or expected replace fingerprint no longer matches."""


class CatalogReplaceConflictError(CatalogDriftError):
    """A compare-and-swap replacement cannot be applied safely."""


class LegacyMigrationError(ExtensionCatalogError):
    """Legacy registry migration failed before promoting any destination state."""

    def __init__(self, message: str, diagnostics: tuple["CatalogDiagnostic", ...] = ()) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics


@dataclass(frozen=True)
class CatalogDiagnostic:
    """Safe structured diagnostic without manifest contents or stack traces."""

    extension_id: str | None
    path: str | None
    flavor: str | None
    state: str
    code: str
    message: str
    expected_fingerprint: str | None = None
    observed_fingerprint: str | None = None


__all__ = [
    "CatalogCodecError",
    "CatalogConflictError",
    "CatalogCorruptError",
    "CatalogDiagnostic",
    "CatalogDriftError",
    "CatalogIdConflictError",
    "CatalogLockBusyError",
    "CatalogLockError",
    "CatalogPathConflictError",
    "CatalogReplaceConflictError",
    "CatalogVersionError",
    "LegacyMigrationError",
    "CatalogManifestError",
    "CatalogManifestIncompatibleError",
    "CatalogManifestInvalidError",
    "CatalogManifestMissingError",
    "CatalogSchemaError",
    "CatalogStorageError",
    "ExtensionCatalogError",
    "WorkspaceCapabilityInvalidError",
    "WorkspaceCapabilityNotDeclaredError",
    "WorkspaceCodecError",
    "WorkspaceConfigurationConflictError",
    "WorkspaceConfigurationCorruptError",
    "WorkspaceExtensionError",
    "WorkspaceExtensionMissingError",
    "WorkspaceExtensionNotConfiguredError",
    "WorkspaceManifestBlockedError",
    "WorkspacePathError",
    "WorkspaceSchemaError",
    "WorkspaceStorageError",
    "WorkspaceVersionError",
]
