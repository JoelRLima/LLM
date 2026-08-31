"""Contratos de runtime independentes do orquestrador."""

from agent.runtime.budget import BudgetExhausted, BudgetSnapshot, TaskBudgetLedger
from agent.runtime.context import (
    Artifact,
    ModelCallBudget,
    RuntimeLimits,
    TaskExecutionContext,
    TaskResult,
    TaskStatus,
)
from agent.runtime.correlation import RunCorrelation
from agent.runtime.event_dispatch import RuntimeEventDispatcher
from agent.runtime.events import RuntimeEvent, RuntimeEventKind
from agent.runtime.hardware import HardwareProfile, resolve_hardware_profile
from agent.runtime.outcome_taxonomy import (
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
from agent.runtime.task_policy import (
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
