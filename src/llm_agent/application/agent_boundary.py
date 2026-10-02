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
    "ApprovalDecision": ("llm_agent.agent.approval", "ApprovalDecision"),
    "ApprovalRequest": ("llm_agent.agent.approval", "ApprovalRequest"),
    "ApprovalWaitCancelled": ("llm_agent.agent.approval", "ApprovalWaitCancelled"),
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
    "HomeLifecycleLease": ("llm_agent.agent.runtime.home_lifecycle", "HomeLifecycleLease"),
    "InspectionQuery": ("llm_agent.agent.presentation", "InspectionQuery"),
    "InspectionService": ("llm_agent.agent.presentation", "InspectionService"),
    "InspectorSnapshot": ("llm_agent.agent.presentation", "InspectorSnapshot"),
    "MODEL_SAFE_TOOL_DESCRIPTORS": ("llm_agent.agent.engineering.model_safe", "MODEL_SAFE_TOOL_DESCRIPTORS"),
    "ModelSafeEngineering": ("llm_agent.agent.engineering.model_safe", "ModelSafeEngineering"),
    "ModelSafeResponse": ("llm_agent.agent.engineering.model_safe", "ModelSafeResponse"),
    "ModelSafeStatus": ("llm_agent.agent.engineering.model_safe", "ModelSafeStatus"),
    "StorageBootstrap": ("llm_agent.agent.runtime.storage_bootstrap", "StorageBootstrap"),
    "TraceCorruptError": ("llm_agent.agent.observability.trace_store", "TraceCorruptError"),
    "TraceUnavailableError": ("llm_agent.agent.observability.trace_store", "TraceUnavailableError"),
    "FaultPlanV1": ("llm_agent.agent.evaluation.practical", "FaultPlanV1"),
    "FaultPlanV1Error": ("llm_agent.agent.evaluation.practical", "FaultPlanV1Error"),
    "build_model_safe_engineering_service": (
        "llm_agent.agent.engineering.model_safe",
        "build_model_safe_engineering_service",
    ),
    "candidate_identity": ("llm_agent.agent.evaluation.evaluation_identity", "candidate_identity"),
    "candidate_identity_string": ("llm_agent.agent.evaluation.evaluation_identity", "candidate_identity_string"),
    "compare_practical_profiles": ("llm_agent.agent.evaluation.practical", "compare_practical_profiles"),
    "parse_fault_json": ("llm_agent.agent.evaluation.practical", "parse_fault_json"),
    "production_registry": ("llm_agent.agent.engineering.registry", "production_registry"),
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
