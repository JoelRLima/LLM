"""Pure resolution of workspace intent, catalog entries and observations."""

from __future__ import annotations

from dataclasses import dataclass, field

from agent.tools.extension_catalog_document import ExtensionCatalogDocument, PersistedCatalogEntry
from agent.tools.extension_catalog_validation import ManifestObservation
from agent.tools.extension_state import WorkspaceExtensionsState


@dataclass(frozen=True)
class WorkspaceResolutionDiagnostic:
    """Safe structured diagnostic for a resolved workspace extension."""

    extension_id: str
    code: str
    severity: str
    safe_message: str
    capability_id: str | None = None


@dataclass(frozen=True)
class ResolvedWorkspaceExtension:
    """All independent dimensions of one workspace extension decision."""

    extension_id: str
    enabled: bool
    configured_grants: tuple[str, ...]
    catalog_entry: PersistedCatalogEntry | None
    catalog_presence: str
    manifest_status: str | None
    required_capabilities: tuple[str, ...]
    effective_grants: tuple[str, ...]
    missing_grants: tuple[str, ...]
    unused_grants: tuple[str, ...]
    activation_status: str
    diagnostics: tuple[WorkspaceResolutionDiagnostic, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ResolvedWorkspaceExtensions:
    """Immutable resolution snapshot with deterministic ordering."""

    entries: tuple[ResolvedWorkspaceExtension, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "entries", tuple(sorted(self.entries, key=lambda item: item.extension_id)))

    def get(self, extension_id: str) -> ResolvedWorkspaceExtension | None:
        return next((item for item in self.entries if item.extension_id == extension_id), None)


def resolve_workspace_extensions(
    state: WorkspaceExtensionsState,
    catalog: ExtensionCatalogDocument,
    observations: tuple[ManifestObservation, ...] | list[ManifestObservation],
) -> ResolvedWorkspaceExtensions:
    """Resolve pure snapshots; this function performs no I/O or persistence."""

    observation_by_id = {item.extension_id: item for item in observations}
    resolved: list[ResolvedWorkspaceExtension] = []
    for selection in state.selections:
        entry = catalog.get(selection.extension_id)
        observation = observation_by_id.get(selection.extension_id)
        required = (
            observation.manifest_summary.required_capabilities if observation is not None else ()
        )
        configured = selection.granted_capabilities
        effective = tuple(sorted(set(configured) & set(required)))
        missing = tuple(sorted(set(required) - set(configured)))
        unused = (
            tuple(sorted(set(configured) - set(required)))
            if observation is not None
            else ()
        )
        diagnostics: list[WorkspaceResolutionDiagnostic] = []
        presence = "present" if entry is not None else "orphaned"
        if entry is None:
            diagnostics.append(
                WorkspaceResolutionDiagnostic(
                    selection.extension_id,
                    "WORKSPACE_EXTENSION_ORPHANED",
                    "error",
                    "Extension configurada não está presente no catálogo.",
                )
            )
        if not selection.enabled:
            diagnostics.append(
                WorkspaceResolutionDiagnostic(
                    selection.extension_id,
                    "WORKSPACE_EXTENSION_DISABLED",
                    "info",
                    "Extension desabilitada neste workspace.",
                )
            )
        if observation is not None and observation.manifest_status != "unchanged":
            code, message = _manifest_diagnostic(observation.manifest_status)
            diagnostics.append(
                WorkspaceResolutionDiagnostic(selection.extension_id, code, "error", message)
            )
        elif entry is not None and observation is None:
            diagnostics.append(
                WorkspaceResolutionDiagnostic(
                    selection.extension_id,
                    "WORKSPACE_EXTENSION_MANIFEST_UNAVAILABLE",
                    "error" if selection.enabled else "warning",
                    "Manifest não foi observado.",
                )
            )
        for capability in missing:
            diagnostics.append(
                WorkspaceResolutionDiagnostic(
                    selection.extension_id,
                    "WORKSPACE_EXTENSION_MISSING_GRANT",
                    "error",
                    "Capability requerida não foi concedida explicitamente.",
                    capability,
                )
            )
        for capability in unused:
            diagnostics.append(
                WorkspaceResolutionDiagnostic(
                    selection.extension_id,
                    "WORKSPACE_EXTENSION_UNUSED_GRANT",
                    "warning",
                    "Grant preservado não é requerido pelo manifest atual.",
                    capability,
                )
            )
        if not selection.enabled:
            status = "disabled"
        elif entry is None or observation is None or observation.manifest_status != "unchanged" or missing:
            status = "blocked"
        else:
            status = "ready"
        resolved.append(
            ResolvedWorkspaceExtension(
                extension_id=selection.extension_id,
                enabled=selection.enabled,
                configured_grants=configured,
                catalog_entry=entry,
                catalog_presence=presence,
                manifest_status=observation.manifest_status if observation else None,
                required_capabilities=required,
                effective_grants=effective,
                missing_grants=missing,
                unused_grants=unused,
                activation_status=status,
                diagnostics=tuple(diagnostics),
            )
        )
    return ResolvedWorkspaceExtensions(tuple(resolved))


def _manifest_diagnostic(status: str) -> tuple[str, str]:
    return {
        "changed": ("WORKSPACE_EXTENSION_MANIFEST_CHANGED", "Manifest mudou desde o registro."),
        "missing": ("WORKSPACE_EXTENSION_MANIFEST_MISSING", "Manifest não está disponível."),
        "invalid": ("WORKSPACE_EXTENSION_MANIFEST_INVALID", "Manifest é inválido."),
        "incompatible": ("WORKSPACE_EXTENSION_INCOMPATIBLE", "Manifest é incompatível."),
    }.get(status, ("WORKSPACE_EXTENSION_MANIFEST_UNAVAILABLE", "Manifest não foi observado."))


__all__ = [
    "ResolvedWorkspaceExtension",
    "ResolvedWorkspaceExtensions",
    "WorkspaceResolutionDiagnostic",
    "resolve_workspace_extensions",
]
