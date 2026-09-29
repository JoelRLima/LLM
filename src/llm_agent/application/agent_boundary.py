"""Finite composition boundary for interface-facing Agent capabilities.

The public interfaces need a small set of Agent semantic types, but the
interface layer must not become an owner of Agent internals.  This module is
the explicit application composition boundary: names are resolved lazily and
only from the allow-listed canonical modules below.  It is intentionally not
a package alias or a compatibility namespace.
"""

from __future__ import annotations

import importlib
from typing import Any

_EXPORTS: dict[str, tuple[str, str]] = {
    "AgentApplication": ("llm_agent.agent.application", "AgentApplication"),
    "ApprovalDecision": ("llm_agent.agent.approval", "ApprovalDecision"),
    "ApprovalRequest": ("llm_agent.agent.approval", "ApprovalRequest"),
    "ApprovalWaitCancelled": ("llm_agent.agent.approval", "ApprovalWaitCancelled"),
    "AutoApprove": ("llm_agent.agent.approval", "AutoApprove"),
    "BookmarkStore": ("llm_agent.agent.observability.bookmarks", "BookmarkStore"),
    "CODE_COMMAND_HELP": ("llm_agent.agent.code.commands", "CODE_COMMAND_HELP"),
    "CODE_TASK_ACTIONS": ("llm_agent.agent.tools.invocation_semantics", "CODE_TASK_ACTIONS"),
    "ChangeApprover": ("llm_agent.agent.code.policy", "ChangeApprover"),
    "ChangePreview": ("llm_agent.agent.code.changes", "ChangePreview"),
    "ChatSession": ("llm_agent.agent.llm.session", "ChatSession"),
    "CodeCommandError": ("llm_agent.agent.code.commands", "CodeCommandError"),
    "CodeRequest": ("llm_agent.agent.code.application", "CodeRequest"),
    "CodingApplicationService": ("llm_agent.agent.code.application", "CodingApplicationService"),
    "ConfigError": ("llm_agent.agent.runtime.config_errors", "ConfigError"),
    "ConfigNotFound": ("llm_agent.agent.runtime.config_errors", "ConfigNotFound"),
    "ConfigRepository": ("llm_agent.agent.runtime.config_repository", "ConfigRepository"),
    "DiagnosticExporter": ("llm_agent.agent.observability.export", "DiagnosticExporter"),
    "EngineeringErrorV1": ("llm_agent.agent.engineering.contracts", "EngineeringErrorV1"),
    "EngineeringCaller": ("llm_agent.agent.engineering.contracts", "EngineeringCaller"),
    "EngineeringExecutionContext": ("llm_agent.agent.engineering.contracts", "EngineeringExecutionContext"),
    "EngineeringOperationViewV1": ("llm_agent.agent.engineering.contracts", "EngineeringOperationViewV1"),
    "EngineeringPermission": ("llm_agent.agent.engineering.contracts", "EngineeringPermission"),
    "EngineeringQueryStatus": ("llm_agent.agent.engineering.contracts", "EngineeringQueryStatus"),
    "EngineeringRequest": ("llm_agent.agent.engineering.contracts", "EngineeringRequest"),
    "EngineeringRunResultV1": ("llm_agent.agent.engineering.contracts", "EngineeringRunResultV1"),
    "EngineeringTerminalStatus": ("llm_agent.agent.engineering.contracts", "EngineeringTerminalStatus"),
    "EngineeringWorkspaceContext": ("llm_agent.agent.engineering.contracts", "EngineeringWorkspaceContext"),
    "SourceRepositoryContext": ("llm_agent.agent.engineering.contracts", "SourceRepositoryContext"),
    "EngineeringRegistry": ("llm_agent.agent.engineering.registry", "EngineeringRegistry"),
    "EngineeringService": ("llm_agent.agent.engineering.service", "EngineeringService"),
    "EngineeringRunStore": ("llm_agent.agent.engineering.store", "EngineeringRunStore"),
    "EvaluationBackend": ("llm_agent.agent.engineering.backends.evaluation", "EvaluationBackend"),
    "HealthBackend": ("llm_agent.agent.engineering.backends.health", "HealthBackend"),
    "InspectionBackend": ("llm_agent.agent.engineering.backends.inspection", "InspectionBackend"),
    "RepositoryBackend": ("llm_agent.agent.engineering.backends.repository", "RepositoryBackend"),
    "ExtensionCatalogService": ("llm_agent.extensions.extension_catalog_service", "ExtensionCatalogService"),
    "ExtensionCatalogStorage": ("llm_agent.extensions.extension_catalog_storage", "ExtensionCatalogStorage"),
    "ExtensionRegistry": ("llm_agent.extensions.extension_registry", "ExtensionRegistry"),
    "HomeLifecycleLease": ("llm_agent.agent.runtime.home_lifecycle", "HomeLifecycleLease"),
    "InspectionQuery": ("llm_agent.agent.presentation", "InspectionQuery"),
    "InspectionService": ("llm_agent.agent.presentation", "InspectionService"),
    "InspectorSnapshot": ("llm_agent.agent.presentation", "InspectorSnapshot"),
    "InstanceLock": ("llm_agent.agent.runtime.instance_lock", "InstanceLock"),
    "MODEL_SAFE_TOOL_DESCRIPTORS": ("llm_agent.agent.engineering.model_safe", "MODEL_SAFE_TOOL_DESCRIPTORS"),
    "ModelConnectionError": ("llm_agent.agent.llm.errors", "ModelConnectionError"),
    "ModelSafeEngineering": ("llm_agent.agent.engineering.model_safe", "ModelSafeEngineering"),
    "ModelSafeResponse": ("llm_agent.agent.engineering.model_safe", "ModelSafeResponse"),
    "ModelSafeStatus": ("llm_agent.agent.engineering.model_safe", "ModelSafeStatus"),
    "ModelTimeoutError": ("llm_agent.agent.llm.errors", "ModelTimeoutError"),
    "OperationalMode": ("llm_agent.agent.tools.authority", "OperationalMode"),
    "Orchestrator": ("llm_agent.agent.orchestrator", "Orchestrator"),
    "ProposalAssessment": ("llm_agent.agent.code.policy", "ProposalAssessment"),
    "RequireExplicitApproval": ("llm_agent.agent.approval", "RequireExplicitApproval"),
    "RuntimeEvent": ("llm_agent.agent.runtime.events", "RuntimeEvent"),
    "RuntimeEventKind": ("llm_agent.agent.runtime.event_kinds", "RuntimeEventKind"),
    "StateMigrationError": ("llm_agent.agent.runtime.state_migration", "StateMigrationError"),
    "StorageBootstrap": ("llm_agent.agent.runtime.storage_bootstrap", "StorageBootstrap"),
    "TaskContextResolver": ("llm_agent.agent.task_definition.resolver", "TaskContextResolver"),
    "TaskContinuityService": ("llm_agent.agent.continuity", "TaskContinuityService"),
    "TaskDefinitionError": ("llm_agent.agent.task_definition.errors", "TaskDefinitionError"),
    "TaskDefinitionRepository": ("llm_agent.agent.task_definition.repository", "TaskDefinitionRepository"),
    "TaskResult": ("llm_agent.agent.runtime.context", "TaskResult"),
    "TaskRunDirective": ("llm_agent.agent.runtime.task_directives", "TaskRunDirective"),
    "TraceCorruptError": ("llm_agent.agent.observability.trace_store", "TraceCorruptError"),
    "TraceUnavailableError": ("llm_agent.agent.observability.trace_store", "TraceUnavailableError"),
    "WorkspaceExtensionService": ("llm_agent.extensions.workspace_extensions_service", "WorkspaceExtensionService"),
    "FaultPlanV1": ("llm_agent.agent.evaluation.practical", "FaultPlanV1"),
    "FaultPlanV1Error": ("llm_agent.agent.evaluation.practical", "FaultPlanV1Error"),
    "bind_worker_output": ("llm_agent.agent.runtime.worker_output", "bind_worker_output"),
    "build_code_context": ("llm_agent.agent.code.application", "build_code_context"),
    "build_model_safe_engineering_service": (
        "llm_agent.agent.engineering.model_safe",
        "build_model_safe_engineering_service",
    ),
    "candidate_identity": ("llm_agent.agent.evaluation.evaluation_identity", "candidate_identity"),
    "candidate_identity_string": ("llm_agent.agent.evaluation.evaluation_identity", "candidate_identity_string"),
    "compare_practical_profiles": ("llm_agent.agent.evaluation.practical", "compare_practical_profiles"),
    "emit_worker_output": ("llm_agent.agent.runtime.worker_output", "emit_worker_output"),
    "load_strict_extension_manifest": ("llm_agent.agent.tools.stdio_adapter", "load_strict_extension_manifest"),
    "logger": ("llm_agent.agent.runtime.logging", "logger"),
    "migrate_legacy_state": ("llm_agent.agent.runtime.state_migration", "migrate_legacy_state"),
    "parse_code_command": ("llm_agent.agent.code.commands", "parse_code_command"),
    "parse_fault_json": ("llm_agent.agent.evaluation.practical", "parse_fault_json"),
    "production_registry": ("llm_agent.agent.engineering.registry", "production_registry"),
    "requests_test_execution": ("llm_agent.agent.tools.mode_enforcement", "requests_test_execution"),
    "resolve_model_profile": ("llm_agent.agent.llm.model_profile", "resolve_model_profile"),
    "run_health_check": ("llm_agent.agent.health_check", "run_health_check"),
    "set_debug_level": ("llm_agent.agent.runtime.logging", "set_debug_level"),
    "validate_extension_id": ("llm_agent.extensions.extension_state", "validate_extension_id"),
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    try:
        module_name, attribute_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    value = getattr(importlib.import_module(module_name), attribute_name)
    globals()[name] = value
    return value
