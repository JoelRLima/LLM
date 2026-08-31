"""Immutable result types for the task-scoped runtime policy."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from agent.runtime.budget import BudgetExhausted


class TaskPolicyDecision(str, Enum):
    ALLOW = "allow"
    TRUNCATE_BATCH = "truncate_batch"
    CANCELLED = "cancelled"
    QUANTITATIVE_EXHAUSTED = "quantitative_exhausted"
    LOGICAL_STEP_EXHAUSTED = "logical_step_exhausted"
    ACTIVE_WALL_EXHAUSTED = "active_wall_exhausted"
    WATCHDOG_NO_PROGRESS = "watchdog_no_progress"
    WATCHDOG_REPEATED_ERROR = "watchdog_repeated_error"
    RECOVERY_EXHAUSTED = "recovery_exhausted"


TERMINAL_STATUS = {
    TaskPolicyDecision.CANCELLED: "cancelled",
    TaskPolicyDecision.QUANTITATIVE_EXHAUSTED: "blocked",
    TaskPolicyDecision.LOGICAL_STEP_EXHAUSTED: "blocked",
    TaskPolicyDecision.ACTIVE_WALL_EXHAUSTED: "timed_out",
    TaskPolicyDecision.WATCHDOG_NO_PROGRESS: "unverified",
    TaskPolicyDecision.WATCHDOG_REPEATED_ERROR: "failed",
    TaskPolicyDecision.RECOVERY_EXHAUSTED: "blocked",
}

REASON_CODE = {
    TaskPolicyDecision.CANCELLED: "TASK_POLICY_CANCELLED",
    TaskPolicyDecision.QUANTITATIVE_EXHAUSTED: "TASK_QUANTITATIVE_BUDGET_EXHAUSTED",
    TaskPolicyDecision.LOGICAL_STEP_EXHAUSTED: "TASK_LOGICAL_STEP_EXHAUSTED",
    TaskPolicyDecision.ACTIVE_WALL_EXHAUSTED: "TASK_ACTIVE_WALL_EXHAUSTED",
    TaskPolicyDecision.WATCHDOG_NO_PROGRESS: "TASK_WATCHDOG_NO_PROGRESS",
    TaskPolicyDecision.WATCHDOG_REPEATED_ERROR: "TASK_WATCHDOG_REPEATED_ERROR",
    TaskPolicyDecision.RECOVERY_EXHAUSTED: "TASK_RECOVERY_EXHAUSTED",
}


@dataclass(frozen=True, slots=True)
class TaskPolicyResult:
    """Immutable result returned by every policy boundary."""

    decision: TaskPolicyDecision
    requested_units: int = 0
    admitted_units: int = 0
    remaining_units: int | None = None
    reason_code: str = ""
    message: str = ""
    terminal_status: str | None = None

    def __post_init__(self) -> None:
        _non_negative_int(self.requested_units, "requested_units")
        _non_negative_int(self.admitted_units, "admitted_units")
        if self.admitted_units > self.requested_units:
            raise ValueError("admitted_units cannot exceed requested_units")
        if self.remaining_units is not None:
            _non_negative_int(self.remaining_units, "remaining_units")
        if not isinstance(self.decision, TaskPolicyDecision):
            object.__setattr__(self, "decision", TaskPolicyDecision(self.decision))
        if not self.reason_code:
            object.__setattr__(self, "reason_code", REASON_CODE.get(self.decision, "TASK_POLICY_ALLOW"))
        if self.terminal_status is None:
            object.__setattr__(self, "terminal_status", TERMINAL_STATUS.get(self.decision))

    @property
    def allowed(self) -> bool:
        return self.decision in {TaskPolicyDecision.ALLOW, TaskPolicyDecision.TRUNCATE_BATCH}

    @property
    def denied(self) -> bool:
        return not self.allowed

    @property
    def truncated(self) -> bool:
        return self.decision is TaskPolicyDecision.TRUNCATE_BATCH

    @property
    def is_terminal(self) -> bool:
        return self.terminal_status is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "requested_units": self.requested_units,
            "admitted_units": self.admitted_units,
            "remaining_units": self.remaining_units,
            "reason_code": self.reason_code,
            "message": self.message,
            "terminal_status": self.terminal_status,
        }


class TaskPolicyError(BudgetExhausted):
    """A denied model admission at a legacy exception boundary."""

    def __init__(self, result: TaskPolicyResult) -> None:
        self.result = result
        super().__init__(result.reason_code or result.decision.value, result.requested_units, result.requested_units)


def _non_negative_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


__all__ = ["TaskPolicyDecision", "TaskPolicyError", "TaskPolicyResult"]
