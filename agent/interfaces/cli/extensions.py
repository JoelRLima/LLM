"""Canonical extension administration for the standalone CLI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from agent.runtime.paths import AppPaths
from agent.runtime.workspace_context import WorkspaceContext
from agent.tools.extension_catalog_service import ExtensionCatalogService
from agent.tools.extension_catalog_storage import ExtensionCatalogStorage
from agent.tools.workspace_extensions_service import WorkspaceExtensionService


def _modern_extension_services(
    app_paths: AppPaths,
    workspace: Path,
) -> tuple[ExtensionCatalogService, WorkspaceExtensionService, str]:
    workspace_id = WorkspaceContext.create(workspace).workspace_id
    catalog = ExtensionCatalogService(
        ExtensionCatalogStorage(app_paths.extensions_catalog_file)
    )
    workspace_service = WorkspaceExtensionService.for_workspace(
        app_paths,
        workspace_id,
        catalog,
    )
    return catalog, workspace_service, workspace_id


def _extension_record(
    entry: Any,
    observation: Any,
    selection: Any,
    resolved: Any,
) -> dict[str, Any]:
    if selection is None:
        workspace = {
            "configured": False,
            "enabled": False,
            "grants": [],
            "activation_status": "not_configured",
        }
    else:
        workspace = {
            "configured": True,
            "enabled": selection.enabled,
            "grants": list(selection.granted_capabilities),
            "activation_status": (
                resolved.activation_status if resolved is not None else "unavailable"
            ),
        }
    return {
        "id": entry.extension_id,
        "manifest": entry.manifest_path.persisted_value,
        "fingerprint": entry.manifest_sha256,
        "manifest_status": observation.manifest_status if observation is not None else "unavailable",
        "required_capabilities": (
            list(observation.manifest_summary.required_capabilities)
            if observation is not None
            else []
        ),
        "workspace": workspace,
    }


def _resolved_record(entry: Any) -> dict[str, Any]:
    return {
        "id": entry.extension_id,
        "enabled": entry.enabled,
        "grants": list(entry.configured_grants),
        "required_capabilities": list(entry.required_capabilities),
        "effective_grants": list(entry.effective_grants),
        "missing_grants": list(entry.missing_grants),
        "unused_grants": list(entry.unused_grants),
        "catalog_presence": entry.catalog_presence,
        "manifest_status": entry.manifest_status,
        "activation_status": entry.activation_status,
        "diagnostics": [
            {"code": item.code, "severity": item.severity, "message": item.safe_message}
            for item in entry.diagnostics
        ],
    }


def _print_extension_payload(payload: Any, *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    if isinstance(payload, dict) and "extensions" in payload:
        for item in payload["extensions"]:
            workspace = item.get("workspace", {})
            print(
                f"{item['id']} [{workspace.get('activation_status', 'unknown')}] "
                f"manifest={item.get('manifest', '')} "
                f"grants={','.join(workspace.get('grants', []))}"
            )
        if not payload["extensions"]:
            print("Nenhuma extension registrada no catalogo moderno.")
        return
    if isinstance(payload, dict) and "extension" in payload:
        item = payload["extension"]
        if item is None:
            print("Extension nao configurada neste workspace.")
        else:
            print(
                f"{item['id']} [{item['activation_status']}] "
                f"grants={','.join(item['grants'])} "
                f"required={','.join(item['required_capabilities'])}"
            )
        return
    print(payload.get("message", payload) if isinstance(payload, dict) else payload)


def run_extensions(
    args: argparse.Namespace,
    *,
    app_paths: AppPaths,
    workspace: Path,
) -> int:
    """Administer the modern catalog/workspace extension state."""

    catalog, workspace_service, workspace_id = _modern_extension_services(app_paths, workspace)
    command = args.extensions_command
    json_output = bool(getattr(args, "json_output", False))

    if command == "register":
        result = catalog.add(args.manifest)
        entry = result.entry
        assert entry is not None
        _print_extension_payload(
            {
                "operation": "register",
                "changed": result.changed,
                "extension_id": entry.extension_id,
                "catalog_path": str(app_paths.extensions_catalog_file),
            },
            json_output=json_output,
        )
        return 0

    if command in {"enable", "disable", "grant", "revoke"}:
        if command == "enable":
            workspace_result = workspace_service.enable(args.id)
        elif command == "disable":
            workspace_result = workspace_service.disable(args.id)
        elif command == "grant":
            workspace_result = workspace_service.grant(args.id, args.capability)
        else:
            workspace_result = workspace_service.revoke(args.id, args.capability)
        selection = workspace_result.selection
        _print_extension_payload(
            {
                "operation": command,
                "changed": workspace_result.changed,
                "extension_id": args.id,
                "workspace_id": workspace_id,
                "enabled": selection.enabled if selection is not None else None,
                "grants": list(selection.granted_capabilities) if selection is not None else [],
            },
            json_output=json_output,
        )
        return 0

    if command == "list":
        document = catalog.load()
        observations = {
            item.extension_id: (item, diagnostic)
            for item, diagnostic in catalog.observe()
        }
        state = workspace_service.load()
        resolved = workspace_service.resolve()
        records = []
        for entry in document.entries:
            observation, diagnostic = observations[entry.extension_id]
            records.append(
                _extension_record(
                    entry,
                    observation,
                    state.get(entry.extension_id),
                    resolved.get(entry.extension_id),
                )
            )
            records[-1]["diagnostic"] = {
                "code": diagnostic.code,
                "state": diagnostic.state,
                "message": diagnostic.message,
            }
        _print_extension_payload(
            {
                "catalog_path": str(app_paths.extensions_catalog_file),
                "workspace_id": workspace_id,
                "extensions": records,
            },
            json_output=json_output,
        )
        return 0

    if command == "inspect":
        resolved = workspace_service.resolve()
        payload: dict[str, Any]
        if args.id is not None:
            from agent.tools.extension_state import validate_extension_id

            validate_extension_id(args.id)
            resolved_entry = resolved.get(args.id)
            payload = {"workspace_id": workspace_id, "extension": _resolved_record(resolved_entry) if resolved_entry else None}
        else:
            payload = {
                "workspace_id": workspace_id,
                "extensions": [_resolved_record(entry) for entry in resolved.entries],
            }
        _print_extension_payload(payload, json_output=json_output)
        return 0

    raise ValueError(f"Comando de extensions desconhecido: {command}")


__all__ = ["run_extensions"]
