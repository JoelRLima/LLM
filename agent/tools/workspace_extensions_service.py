"""Administrative operations for one workspace's extension configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from agent.runtime.paths import AppPaths, WorkspacePaths
from agent.tools.extension_catalog_errors import (
    CatalogDiagnostic,
    WorkspaceCapabilityInvalidError,
    WorkspaceCapabilityNotDeclaredError,
    WorkspaceExtensionMissingError,
    WorkspaceExtensionNotConfiguredError,
    WorkspaceManifestBlockedError,
    WorkspacePathError,
    WorkspaceStorageError,
)
from agent.tools.extension_catalog_lock import ExtensionCatalogLock
from agent.tools.extension_catalog_service import ExtensionCatalogService
from agent.tools.extension_catalog_validation import ManifestObservation, observe_catalog_document
from agent.tools.extension_state import (
    WorkspaceExtensionSelection,
    WorkspaceExtensionsState,
    validate_capability_id,
    validate_extension_id,
)
from agent.tools.workspace_extensions_resolver import (
    ResolvedWorkspaceExtensions,
    resolve_workspace_extensions,
)
from agent.tools.workspace_extensions_storage import WorkspaceExtensionsStorage


@dataclass(frozen=True)
class WorkspaceOperationResult:
    """Result of an idempotent workspace configuration mutation."""

    state: WorkspaceExtensionsState
    changed: bool
    selection: WorkspaceExtensionSelection | None = None


class _WorkspaceStorageLike(Protocol):
    path: Path

    def load(self) -> WorkspaceExtensionsState: ...

    def save(self, state: WorkspaceExtensionsState) -> None: ...


class _WorkspaceLockLike(Protocol):
    path: Path

    def acquire(self) -> None: ...

    def release(self) -> None: ...

    def __enter__(self) -> "_WorkspaceLockLike": ...

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None: ...


class WorkspaceExtensionService:
    """Coordinate workspace state with, but never mutate, the global catalog."""

    def __init__(
        self,
        app_paths: AppPaths,
        workspace_id: str,
        catalog_service: ExtensionCatalogService,
    ) -> None:
        workspace_paths = app_paths.for_workspace(workspace_id)
        self._initialize(app_paths, workspace_id, workspace_paths, catalog_service)

    def _initialize(
        self,
        app_paths: AppPaths,
        workspace_id: str,
        workspace_paths: WorkspacePaths,
        catalog_service: ExtensionCatalogService,
        *,
        storage: _WorkspaceStorageLike | None = None,
        lock: _WorkspaceLockLike | None = None,
    ) -> None:
        self.workspace_paths = workspace_paths
        self._app_paths = app_paths
        self._workspace_id = workspace_id
        self.catalog_service = catalog_service
        self.storage = storage or WorkspaceExtensionsStorage(
            workspace_paths.workspace_extensions_file
        )
        self.lock = lock or ExtensionCatalogLock(workspace_paths.workspace_extensions_lock_file)

    @classmethod
    def for_workspace(
        cls,
        app_paths: AppPaths,
        workspace_id: str,
        catalog_service: ExtensionCatalogService,
    ) -> "WorkspaceExtensionService":
        """Create the operational service from canonical application paths."""
        return cls(app_paths, workspace_id, catalog_service)

    @staticmethod
    def _validate_test_dependencies(
        workspace_paths: WorkspacePaths,
        storage: _WorkspaceStorageLike | None,
        lock: _WorkspaceLockLike | None,
    ) -> None:
        canonical_file = workspace_paths.workspace_extensions_file
        canonical_lock = workspace_paths.workspace_extensions_lock_file
        if storage is not None and storage.path != canonical_file:
            raise WorkspacePathError("Storage de teste não corresponde ao workspace canônico.")
        if lock is not None and lock.path != canonical_lock:
            raise WorkspacePathError("Lock de teste não corresponde ao workspace canônico.")

    @classmethod
    def _for_testing(
        cls,
        app_paths: AppPaths,
        workspace_id: str,
        catalog_service: ExtensionCatalogService,
        *,
        storage: _WorkspaceStorageLike | None = None,
        lock: _WorkspaceLockLike | None = None,
    ) -> "WorkspaceExtensionService":
        """Build a canonical service with failure-injection dependencies for tests."""

        workspace_paths = app_paths.for_workspace(workspace_id)
        cls._validate_test_dependencies(workspace_paths, storage, lock)
        instance = cls.__new__(cls)
        instance._initialize(
            app_paths,
            workspace_id,
            workspace_paths,
            catalog_service,
            storage=storage,
            lock=lock,
        )
        return instance

    def load(self) -> WorkspaceExtensionsState:
        return self.storage.load()

    def resolve(self) -> ResolvedWorkspaceExtensions:
        """Load current snapshots and delegate decision-making to the pure resolver."""

        state = self.storage.load()
        with self.catalog_service.lock:
            document = self.catalog_service.storage.load()
            pairs = observe_catalog_document(document, self.catalog_service.host_flavor)
        return resolve_workspace_extensions(state, document, tuple(item for item, _ in pairs))

    def inspect(self) -> tuple[WorkspaceExtensionsState | None, tuple[CatalogDiagnostic, ...]]:
        try:
            return self.load(), ()
        except WorkspaceStorageError as exc:
            return None, (
                CatalogDiagnostic(
                    extension_id=None,
                    path=None,
                    flavor=None,
                    state="invalid",
                    code=type(exc).__name__,
                    message="Configuração do workspace inválida.",
                ),
            )

    def enable(self, extension_id: str) -> WorkspaceOperationResult:
        validate_extension_id(extension_id)
        with self.lock:
            state = self.storage.load()
            self._require_current_manifest(extension_id)
            existing = state.get(extension_id)
            if existing is not None and existing.enabled:
                return WorkspaceOperationResult(state, False, existing)
            selection = WorkspaceExtensionSelection(
                extension_id,
                existing.granted_capabilities if existing is not None else (),
                True,
            )
            updated = WorkspaceExtensionsState(
                tuple(
                    selection if item.extension_id == extension_id else item
                    for item in state.selections
                )
                if existing is not None
                else state.selections + (selection,),
            )
            self.storage.save(updated)
            return WorkspaceOperationResult(updated, True, selection)

    def disable(self, extension_id: str) -> WorkspaceOperationResult:
        validate_extension_id(extension_id)
        with self.lock:
            state = self.storage.load()
            existing = state.get(extension_id)
            if existing is None or not existing.enabled:
                return WorkspaceOperationResult(state, False, existing)
            updated = state.disable(extension_id)
            self.storage.save(updated)
            return WorkspaceOperationResult(updated, True, updated.get(extension_id))

    def grant(self, extension_id: str, capability: str) -> WorkspaceOperationResult:
        validate_extension_id(extension_id)
        capability = self._validate_capability(capability)
        with self.lock:
            state = self.storage.load()
            existing = state.get(extension_id)
            if existing is None:
                raise WorkspaceExtensionNotConfiguredError(
                    f"Extension não configurada no workspace: {extension_id}"
                )
            observation = self._require_current_manifest(extension_id)
            required = observation.manifest_summary.required_capabilities
            if capability not in required:
                raise WorkspaceCapabilityNotDeclaredError(
                    "Capability não declarada pelo manifest atual"
                )
            if capability in existing.granted_capabilities:
                return WorkspaceOperationResult(state, False, existing)
            selection = WorkspaceExtensionSelection(
                extension_id,
                (*existing.granted_capabilities, capability),
                existing.enabled,
            )
            updated = WorkspaceExtensionsState(
                tuple(selection if item.extension_id == extension_id else item for item in state.selections)
            )
            self.storage.save(updated)
            return WorkspaceOperationResult(updated, True, selection)

    def revoke(self, extension_id: str, capability: str) -> WorkspaceOperationResult:
        validate_extension_id(extension_id)
        capability = self._validate_capability(capability)
        with self.lock:
            state = self.storage.load()
            existing = state.get(extension_id)
            if existing is None or capability not in existing.granted_capabilities:
                return WorkspaceOperationResult(state, False, existing)
            selection = WorkspaceExtensionSelection(
                extension_id,
                tuple(item for item in existing.granted_capabilities if item != capability),
                existing.enabled,
            )
            updated = WorkspaceExtensionsState(
                tuple(selection if item.extension_id == extension_id else item for item in state.selections)
            )
            self.storage.save(updated)
            return WorkspaceOperationResult(updated, True, selection)

    def remove_configuration(self, extension_id: str) -> WorkspaceOperationResult:
        validate_extension_id(extension_id)
        with self.lock:
            state = self.storage.load()
            existing = state.get(extension_id)
            if existing is None:
                return WorkspaceOperationResult(state, False)
            updated = WorkspaceExtensionsState(
                tuple(item for item in state.selections if item.extension_id != extension_id)
            )
            self.storage.save(updated)
            return WorkspaceOperationResult(updated, True, existing)

    def _require_current_manifest(self, extension_id: str) -> ManifestObservation:
        with self.catalog_service.lock:
            document = self.catalog_service.storage.load()
            observations = observe_catalog_document(document, self.catalog_service.host_flavor)
        entry = document.get(extension_id)
        if entry is None:
            raise WorkspaceExtensionMissingError(
                f"Extension não está presente no catálogo: {extension_id}"
            )
        observation = next(item for item, _ in observations if item.extension_id == extension_id)
        if observation.manifest_status != "unchanged":
            raise WorkspaceManifestBlockedError(
                "Manifest não está válido e inalterado para esta operação"
            )
        return observation

    @staticmethod
    def _validate_capability(capability: str) -> str:
        try:
            return cast(str, validate_capability_id(capability))
        except (TypeError, ValueError) as exc:
            raise WorkspaceCapabilityInvalidError("Capability inválida") from exc


__all__ = ["WorkspaceExtensionService", "WorkspaceOperationResult"]
