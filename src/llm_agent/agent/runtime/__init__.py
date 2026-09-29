"""Contratos de runtime independentes do orquestrador."""

from llm_agent.agent.runtime.budget import BudgetExhausted, BudgetSnapshot, TaskBudgetLedger
from llm_agent.agent.runtime.context import (
    Artifact,
    ModelCallBudget,
    RuntimeLimits,
    TaskExecutionContext,
    TaskResult,
    TaskStatus,
)
from llm_agent.agent.runtime.correlation import RunCorrelation
from llm_agent.agent.runtime.event_dispatch import RuntimeEventDispatcher
from llm_agent.agent.runtime.events import RuntimeEvent, RuntimeEventKind
from llm_agent.agent.runtime.hardware import HardwareProfile, resolve_hardware_profile
from llm_agent.agent.runtime.outcome_taxonomy import (
    ERROR_DEFINITIONS,
    HARD_FAILURE_CODES,
    HARD_FAILURE_STATUSES,
    LOCAL_FAILURE_STATUSES,
    NON_SUCCESS_STATUSES,
    PUBLIC_ERROR_CODES,
    PUBLIC_TERMINAL_STATUSES,
    OperationalStatus,
    error_definition,
    failure_layer_for_code,
    operational_status_for,
)
from llm_agent.agent.runtime.task_policy import (
    PolicyDecision,
    PolicyResult,
    TaskPolicy,
    TaskPolicyDecision,
    TaskPolicyError,
    TaskPolicyResult,
    TaskPolicyState,
    TaskRuntimePolicy,
)

__all__ = [
    "Artifact",
    "BudgetExhausted",
    "BudgetSnapshot",
    "HardwareProfile",
    "ModelCallBudget",
    "RuntimeLimits",
    "RunCorrelation",
    "RuntimeEvent",
    "RuntimeEventDispatcher",
    "RuntimeEventKind",
    "TaskExecutionContext",
    "TaskBudgetLedger",
    "TaskResult",
    "TaskStatus",
    "ERROR_DEFINITIONS",
    "HARD_FAILURE_CODES",
    "HARD_FAILURE_STATUSES",
    "LOCAL_FAILURE_STATUSES",
    "NON_SUCCESS_STATUSES",
    "OperationalStatus",
    "PUBLIC_ERROR_CODES",
    "PUBLIC_TERMINAL_STATUSES",
    "error_definition",
    "failure_layer_for_code",
    "operational_status_for",
    "resolve_hardware_profile",
    "PolicyDecision",
    "PolicyResult",
    "TaskPolicy",
    "TaskPolicyDecision",
    "TaskPolicyError",
    "TaskPolicyResult",
    "TaskPolicyState",
    "TaskRuntimePolicy",
]
