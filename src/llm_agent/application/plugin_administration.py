"""Local plugin administration over the existing catalog and workspace owners."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from llm_agent.application.plugin_trust import execution_preview
from llm_agent.extensions.extension_catalog_service import ExtensionCatalogService
from llm_agent.extensions.extension_catalog_storage import ExtensionCatalogStorage
from llm_agent.extensions.extension_catalog_validation import observe_catalog_document
from llm_agent.extensions.workspace_extensions_resolver import resolve_workspace_extensions
from llm_agent.extensions.workspace_extensions_service import WorkspaceExtensionService
from llm_agent.workspace.context import WorkspaceContext
from llm_agent.workspace.paths import AppPaths


class PluginAdministration:
    def __init__(self, paths: AppPaths, workspace: WorkspaceContext) -> None:
        self.workspace = workspace
        self.catalog = ExtensionCatalogService(ExtensionCatalogStorage(paths.extensions_catalog_file))
        self.selections = WorkspaceExtensionService.for_workspace(paths, workspace.workspace_id, self.catalog)

    def snapshot(self) -> dict[str, Any]:
        document = self.catalog.load()
        pairs = observe_catalog_document(document, self.catalog.host_flavor)
        observations = {item.extension_id: item for item, _ in pairs}
        resolved = resolve_workspace_extensions(self.selections.load(), document, tuple(observations.values()))
        rows = []
        for extension_id in sorted(
            {item.extension_id for item in document.entries} | {item.extension_id for item in resolved.entries}
        ):
            entry = document.get(extension_id)
            selection = resolved.get(extension_id)
            observed = observations.get(extension_id)
            rows.append(
                {
                    "extension_id": extension_id,
                    "manifest_path": entry.manifest_path.persisted_value if entry else None,
                    "manifest_sha256": entry.manifest_sha256 if entry else None,
                    "manifest_status": observed.manifest_status if observed else "unavailable",
                    "observed_sha256": observed.observed_fingerprint if observed else None,
                    "required_capabilities": list(observed.manifest_summary.required_capabilities) if observed else [],
                    "grants": list(selection.configured_grants) if selection else [],
                    "enabled": bool(selection and selection.enabled),
                    "activation_status": selection.activation_status if selection else "not_enabled",
                    "diagnostic_codes": [item.code for item in selection.diagnostics] if selection else [],
                    "execution": execution_preview(entry.manifest_path.persisted_value if entry else None,
                                                   entry.manifest_sha256 if entry else None, self.workspace.root),
                }
            )
        return {"workspace_id": self.workspace.workspace_id, "items": rows}

    def replace_manifest(self, extension_id: str, old_fingerprint: str, reviewed_fingerprint: str) -> None:
        entry = self.catalog.load().get(extension_id)
        if entry is None:
            raise ValueError("Plugin missing from catalog")
        self.catalog.replace(
            extension_id,
            entry.manifest_path.persisted_value,
            expected_fingerprint=old_fingerprint,
            expected_replacement_fingerprint=reviewed_fingerprint,
        )

    def apply(self, action: str, extension_id: str, value: str) -> None:
        if action == "register":
            if not Path(value).is_absolute():
                raise ValueError("Informe o caminho absoluto do manifest.")
            self.catalog.add(value)
        elif action == "remove":
            self.catalog.remove(extension_id)
            self.selections.remove_configuration(extension_id)
        elif action == "forget":
            self.selections.remove_configuration(extension_id)
        elif action == "enable":
            self.selections.enable(extension_id)
        elif action == "disable":
            self.selections.disable(extension_id)
        elif action == "grant":
            self.selections.grant(extension_id, value)
        elif action == "revoke":
            self.selections.revoke(extension_id, value)
        else:
            raise ValueError("Operacao de plugin desconhecida.")
