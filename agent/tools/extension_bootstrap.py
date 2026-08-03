"""Degraded, deterministic composition of workspace extensions at bootstrap."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent.runtime.paths import AppPaths
from agent.tools.authority import ApplicationAuthoritySnapshot
from agent.tools.contracts import ToolAdapter
from agent.tools.extension_catalog_errors import (
    CatalogCodecError,
    CatalogCorruptError,
    CatalogStorageError,
    WorkspaceStorageError,
)
from agent.tools.extension_catalog_service import ExtensionCatalogService
from agent.tools.extension_catalog_storage import ExtensionCatalogStorage
from agent.tools.extension_catalog_validation import observe_catalog_document
from agent.tools.extension_runtime import (
    ExtensionRuntimeBinding,
    ExtensionRuntimeDiagnostic,
    ExtensionRuntimeMaterialization,
    ExtensionRuntimeMaterializer,
)
from agent.tools.runtime_identity import RuntimeSnapshotIdentity
from agent.tools.tool_registry import ToolRegistry
from agent.tools.workspace_extensions_resolver import resolve_workspace_extensions
from agent.tools.workspace_extensions_service import WorkspaceExtensionService


@dataclass(frozen=True)
class ExtensionBootstrapResult:
    """Immutable application bootstrap result and safe diagnostics."""

    registry: ToolRegistry
    materialization: ExtensionRuntimeMaterialization
    diagnostics: tuple[ExtensionRuntimeDiagnostic, ...] = ()
    authority: ApplicationAuthoritySnapshot | None = None


class WorkspaceToolRegistryComposer:
    """Compose builtins and complete extension bindings without partial publication."""

    def compose(
        self,
        builtin_adapter: ToolAdapter,
        materialization: ExtensionRuntimeMaterialization,
        *,
        runtime_identity: RuntimeSnapshotIdentity | None = None,
    ) -> ExtensionBootstrapResult:
        registry = ToolRegistry(runtime_identity=runtime_identity)
        registry.register_adapter(builtin_adapter)
        builtin_names = set(registry.names())
        bindings = tuple(sorted(materialization.bindings, key=lambda item: item.extension_id))
        rejected, diagnostics = self._preflight(bindings, builtin_names, materialization.diagnostics)
        diagnostics.extend(self._register_bindings(registry, bindings, rejected))
        registry.freeze()
        return ExtensionBootstrapResult(
            registry=registry,
            materialization=materialization,
            diagnostics=tuple(diagnostics),
        )

    def _preflight(
        self,
        bindings: tuple[ExtensionRuntimeBinding, ...],
        builtin_names: set[str],
        initial_diagnostics: tuple[ExtensionRuntimeDiagnostic, ...],
    ) -> tuple[set[str], list[ExtensionRuntimeDiagnostic]]:
        diagnostics = list(initial_diagnostics)
        extension_by_name: dict[str, set[str]] = {}
        rejection_reasons: dict[str, set[str]] = {}
        for binding in bindings:
            names = [descriptor.name for descriptor in binding.descriptors]
            if len(names) != len(set(names)):
                rejection_reasons.setdefault(binding.extension_id, set()).add("internal")
            if set(names) & builtin_names:
                rejection_reasons.setdefault(binding.extension_id, set()).add("builtin")
            for name in names:
                extension_by_name.setdefault(name, set()).add(binding.extension_id)
        for extension_ids in extension_by_name.values():
            if len(extension_ids) > 1:
                for extension_id in extension_ids:
                    rejection_reasons.setdefault(extension_id, set()).add("extension")
        rejected = set(rejection_reasons)
        diagnostics.extend(
            self._collision(extension_id) for extension_id in sorted(rejected)
        )
        return rejected, diagnostics

    def _register_bindings(
        self,
        registry: ToolRegistry,
        bindings: tuple[ExtensionRuntimeBinding, ...],
        rejected: set[str],
    ) -> list[ExtensionRuntimeDiagnostic]:
        diagnostics: list[ExtensionRuntimeDiagnostic] = []
        for binding in bindings:
            if binding.extension_id in rejected:
                continue
            try:
                registry.register_adapter(binding.adapter)
            except ValueError:
                diagnostics.append(self._collision(binding.extension_id))
                rejected.add(binding.extension_id)
        return diagnostics

    @staticmethod
    def _collision(extension_id: str) -> ExtensionRuntimeDiagnostic:
        return ExtensionRuntimeDiagnostic(
            extension_id=extension_id,
            code="EXTENSION_RUNTIME_COLLISION",
            severity="error",
            safe_message="Extensão rejeitada por colisão de nome de tool.",
        )


class ApplicationExtensionBootstrap:
    """Load, resolve, materialize and compose one workspace snapshot."""

    def __init__(self, app_paths: AppPaths, workspace_id: str, workspace_root: str | Path) -> None:
        self.app_paths = app_paths
        self.workspace_id = workspace_id
        self.workspace_root = Path(workspace_root).absolute()

    def build(self, builtin_adapter: ToolAdapter) -> ExtensionBootstrapResult:
        empty = ExtensionRuntimeMaterialization()
        runtime_identity = RuntimeSnapshotIdentity.create(self.workspace_id)
        catalog = ExtensionCatalogService(ExtensionCatalogStorage(self.app_paths.extensions_catalog_file))
        try:
            catalog_document = catalog.storage.load()
            observations = tuple(
                item for item, _ in observe_catalog_document(catalog_document, catalog.host_flavor)
            )
            workspace_service = WorkspaceExtensionService.for_workspace(
                self.app_paths, self.workspace_id, catalog
            )
            state = workspace_service.load()
            resolved = resolve_workspace_extensions(state, catalog_document, observations)
        except (CatalogCodecError, CatalogCorruptError):
            return self._degraded(builtin_adapter, empty, "EXTENSION_BOOTSTRAP_CATALOG_CORRUPT", runtime_identity)
        except CatalogStorageError:
            return self._degraded(builtin_adapter, empty, "EXTENSION_BOOTSTRAP_CATALOG_UNAVAILABLE", runtime_identity)
        except WorkspaceStorageError:
            return self._degraded(builtin_adapter, empty, "EXTENSION_BOOTSTRAP_WORKSPACE_CORRUPT", runtime_identity)

        materialization = ExtensionRuntimeMaterializer(
            self.workspace_root, host_flavor=catalog.host_flavor
        ).materialize(resolved)
        composition = WorkspaceToolRegistryComposer().compose(
            builtin_adapter, materialization, runtime_identity=runtime_identity
        )
        resolution_diagnostics = tuple(
            ExtensionRuntimeDiagnostic(
                extension_id=entry.extension_id,
                code=diagnostic.code,
                severity=diagnostic.severity,
                safe_message=diagnostic.safe_message,
                tool_name=None,
            )
            for entry in resolved.entries
            for diagnostic in entry.diagnostics
        )
        return ExtensionBootstrapResult(
            registry=composition.registry,
            materialization=composition.materialization,
            diagnostics=(*resolution_diagnostics, *composition.diagnostics),
            authority=ApplicationAuthoritySnapshot.from_resolved(
                self.workspace_id,
                resolved,
                runtime_identity=runtime_identity,
                provenance="application_extension_bootstrap",
            ),
        )

    def _degraded(
        self,
        builtin_adapter: ToolAdapter,
        materialization: ExtensionRuntimeMaterialization,
        code: str,
        runtime_identity: RuntimeSnapshotIdentity,
    ) -> ExtensionBootstrapResult:
        diagnostic = ExtensionRuntimeDiagnostic(
            extension_id=None,
            code=code,
            severity="error",
            safe_message="O subsistema de extensions não pôde ser carregado; builtins preservados.",
        )
        result = WorkspaceToolRegistryComposer().compose(
            builtin_adapter, materialization, runtime_identity=runtime_identity
        )
        return ExtensionBootstrapResult(
            registry=result.registry,
            materialization=materialization,
            diagnostics=(diagnostic, *result.diagnostics),
            authority=ApplicationAuthoritySnapshot(
                runtime_identity=runtime_identity,
                extension_grants={},
                provenance="application_extension_bootstrap_degraded",
            ),
        )


__all__ = [
    "ApplicationExtensionBootstrap",
    "ExtensionBootstrapResult",
    "WorkspaceToolRegistryComposer",
]
