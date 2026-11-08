"""Read-only projection of existing tool and extension catalogs."""

from __future__ import annotations

from llm_agent.application.extensions import (
    ExtensionCatalogService,
    ExtensionCatalogStorage,
    WorkspaceExtensionService,
)
from llm_agent.application.task_execution import TaskExecutionRuntime, read_execution_context
from llm_agent.application.workbench.contracts import PanelContent, PanelReference


def read_inventory(runtime: TaskExecutionRuntime) -> PanelContent:
    context = read_execution_context(runtime)
    lines = [
        "Ferramentas publicadas pelo runtime (sujeitas à autoridade, capabilities e modo da sessão):",
    ]
    for tool in runtime._owner.tool_registry.descriptors():
        capabilities = ", ".join(sorted(tool.capabilities)) or "nenhuma declarada"
        origin = tool.origin_kind.value
        if tool.extension_id:
            origin += f" ({tool.extension_id})"
        lines.append(
            f"• {tool.name} [{origin}] • categoria={tool.category} • capabilities={capabilities}"
        )
        lines.append(f"  {tool.description}")
        lines.append(
            "  Permissões de escrita/efeitos não são concedidas pelo Workbench; "
            f"cancelamento={tool.cancellation_safety.value}."
        )
    catalog = ExtensionCatalogService(ExtensionCatalogStorage(context.app_paths.extensions_catalog_file))
    service = WorkspaceExtensionService.for_workspace(context.app_paths, context.workspace.workspace_id, catalog)
    lines.append("\nExtensões de execução e decisão de grants neste workspace:")
    entries = service.resolve().entries
    for item in entries:
        configured = ", ".join(item.configured_grants) or "nenhum"
        required = ", ".join(item.required_capabilities) or "nenhuma observada"
        effective = ", ".join(item.effective_grants) or "nenhum"
        missing = ", ".join(item.missing_grants) or "nenhum"
        lines.append(
            f"• {item.extension_id}: {item.activation_status} • enabled={item.enabled} "
            f"• catálogo={item.catalog_presence} • manifest={item.manifest_status or 'não observado'}"
        )
        lines.append(
            f"  grants configurados={configured} • requeridos={required} "
            f"• efetivos={effective} • ausentes={missing}"
        )
        for diagnostic in item.diagnostics:
            capability = f" • capability={diagnostic.capability_id}" if diagnostic.capability_id else ""
            lines.append(f"  diagnóstico [{diagnostic.severity}] {diagnostic.code}{capability}: {diagnostic.safe_message}")
    if not entries:
        lines.append("Nenhuma extensão configurada.")
    reference = PanelReference(context.workspace.workspace_id, None, "projection:runtime-inventory",
                               "Ferramentas e extensões", "ToolRegistry / WorkspaceExtensionService",
                               "text", "unknown", "runtime-inventory")
    return PanelContent(reference, "\n".join(lines), "text", None, False)
