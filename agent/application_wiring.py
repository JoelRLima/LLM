"""Runtime orchestration wiring and configuration helpers for AgentApplication."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.approval import ApprovalPort
from agent.llm.session import ChatSession
from agent.orchestrator import Orchestrator
from agent.runtime.config_repository import ConfigRepository
from agent.runtime.instance_lock import InstanceLock
from agent.runtime.paths import AppPaths, WorkspacePaths
from agent.runtime.workspace_context import WorkspaceContext
from agent.tools.authority import (
    ApplicationAuthoritySnapshot,
    TaskAuthoritySnapshot,
    bind_task_authority,
)
from agent.tools.invocation_gateway import ToolInvocationGateway
from agent.tools.tool_registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class RuntimeOrchestrationComponents:
    orchestrator: Orchestrator
    tool_registry: ToolRegistry
    tool_invocation_gateway: ToolInvocationGateway
    application_authority: ApplicationAuthoritySnapshot
    task_authority: TaskAuthoritySnapshot | None
    bootstrap_diagnostics: tuple[object, ...]


def apply_workspace_paths(config: dict[str, Any], workspace_paths: WorkspacePaths) -> None:
    config["checkpoint_file"] = str(workspace_paths.checkpoint_file)
    raw_report = config.get("task_report")
    report = raw_report if isinstance(raw_report, dict) else {}
    report["output_dir"] = str(workspace_paths.reports_dir)
    config["task_report"] = report


def prepare_application_environment(
    app_paths: AppPaths,
    workspace: str | Path,
    config_path: str | Path | None,
    profile: str | None,
    overrides: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], WorkspaceContext, WorkspacePaths, InstanceLock]:
    config_overrides = dict(overrides or {})
    if profile is not None:
        config_overrides["default_model_profile"] = profile
    repository = ConfigRepository(app_paths, config_path=config_path)
    config = repository.load(overrides=config_overrides).to_dict()
    app_paths.ensure_base_directories()
    workspace_context = WorkspaceContext.create(workspace)
    workspace_paths = app_paths.for_workspace(workspace_context.workspace_id)
    workspace_paths.ensure_directories()
    instance_lock = InstanceLock.create(workspace_paths.lock_file)
    instance_lock.acquire()
    return config, workspace_context, workspace_paths, instance_lock


def build_application_extensions(
    app_paths: AppPaths,
    workspace_context: WorkspaceContext,
    skill_registry: Any,
) -> tuple[Any, Any]:
    """Build the trusted Engineering adapters and the workspace tool registry."""

    from agent.engineering.model_safe import (
        build_model_safe_engineering_service,
        build_model_safe_internal_adapters,
    )
    from agent.tools.builtin_adapter import BuiltinToolAdapter
    from agent.tools.extension_bootstrap import ApplicationExtensionBootstrap

    model_safe_engineering = build_model_safe_engineering_service(
        app_paths=app_paths,
        workspace=workspace_context,
    )
    internal_adapters = build_model_safe_internal_adapters(model_safe_engineering)
    extension_bootstrap = ApplicationExtensionBootstrap(
        app_paths,
        workspace_context.workspace_id,
        workspace_context.root,
        internal_adapters=internal_adapters,
    ).build(BuiltinToolAdapter(skill_registry))
    return model_safe_engineering, extension_bootstrap


def wire_runtime_orchestration(
    *,
    workspace_context: WorkspaceContext,
    workspace_paths: WorkspacePaths,
    session: ChatSession,
    persona_router: Any,
    selected_approval: ApprovalPort,
    task_authority: TaskAuthoritySnapshot | None,
    task_authority_capabilities: Iterable[str] | None,
    extension_bootstrap: Any,
    skill_registry: Any,
) -> RuntimeOrchestrationComponents:
    if task_authority is not None and task_authority_capabilities is not None:
        raise ValueError("forneca task_authority ou task_authority_capabilities, nao ambos")
    selected_task_authority = (
        task_authority
        if task_authority_capabilities is None
        else bind_task_authority(
            task_authority_capabilities,
            extension_bootstrap.authority,
            policy_source="cli.task_authority",
        )
    )
    tool_registry = extension_bootstrap.registry
    orchestrator = Orchestrator(
        session,
        skill_registry=skill_registry,
        tool_registry=tool_registry,
        verbose=False,
        workspace_root=workspace_context.root,
        workspace_paths=workspace_paths,
        application_authority=extension_bootstrap.authority,
        task_authority=selected_task_authority,
        persona_router=persona_router,
    )
    tool_invocation_gateway = ToolInvocationGateway(
        tool_registry,
        budget_ledger=orchestrator.task_budget,
        application_authority=extension_bootstrap.authority,
        task_authority=selected_task_authority,
        approval_port=selected_approval,
        event_dispatcher=orchestrator.event_dispatcher,
        correlation_provider=lambda: orchestrator.run_correlation,
        event_fields_provider=lambda: {
            "plan_id": getattr(orchestrator.agent_state, "plan_identity", None),
            "step_id": getattr(orchestrator.agent_state, "current_step_id", None),
        },
        correlated_state_recorder=lambda name, args, res, correlation: orchestrator.agent_state.record_tool_result(
            name, args, res, correlation=correlation
        ),
        incident_recorder=orchestrator.agent_state.record_execution_incident,
    )
    orchestrator.tool_registry = tool_registry
    orchestrator.tool_invocation_gateway = tool_invocation_gateway
    orchestrator._restore_memory_from_file()
    for skill in skill_registry.skills():
        if hasattr(skill, "orchestrator"):
            skill.orchestrator = orchestrator
    return RuntimeOrchestrationComponents(
        orchestrator=orchestrator,
        tool_registry=tool_registry,
        tool_invocation_gateway=tool_invocation_gateway,
        application_authority=extension_bootstrap.authority,
        task_authority=selected_task_authority,
        bootstrap_diagnostics=extension_bootstrap.diagnostics,
    )


__all__ = [
    "RuntimeOrchestrationComponents",
    "apply_workspace_paths",
    "build_application_extensions",
    "prepare_application_environment",
    "wire_runtime_orchestration",
]
