"""Typed domain projections for bounded plan recovery."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from agent.planning.plan_model import Plan
from agent.runtime.failures import FailureFact
from agent.tools.contracts import ToolResult


class ErrorCategory(Enum):
    """Small domain projection of a canonical :class:`FailureFact`."""

    FILE_NOT_FOUND = "FileNotFoundError"
    SANDBOX = "SandboxError"
    SCHEMA = "SchemaError"
    TOOL_BLOCKED = "ToolBlocked"
    TIMEOUT = "TimeoutError"
    UNKNOWN = "Unknown"


def category_for_failure(failure: FailureFact) -> ErrorCategory:
    """Project canonical code/status into the replan domain vocabulary."""

    if failure.code == "FILE_NOT_FOUND":
        return ErrorCategory.FILE_NOT_FOUND
    if failure.status == "timed_out" or failure.code in {"TIMEOUT", "WATCHDOG_TIMEOUT"}:
        return ErrorCategory.TIMEOUT
    if failure.code in {"INVALID_ARGUMENTS", "MISSING_REQUIRED_INPUT", "REQUEST_INVALID"}:
        return ErrorCategory.SCHEMA
    if failure.status == "permission_denied" or failure.code in {
        "AUTH_DENIED",
        "AUTHORITY_REQUIRED",
        "APPLICATION_AUTHORITY_DENIED",
        "DENIED",
        "OPERATIONAL_MODE_DENIED",
        "PERMISSION_DENIED",
        "TASK_AUTHORITY_DENIED",
        "WORKSPACE_GRANT_DENIED",
    }:
        return ErrorCategory.TOOL_BLOCKED
    if failure.code in {"TOOL_BLOCKED", "TOOL_UNAVAILABLE"}:
        return ErrorCategory.TOOL_BLOCKED
    return ErrorCategory.UNKNOWN


def _readonly_step(value: Mapping[str, Any]) -> Mapping[str, Any]:
    copied = dict(value)
    raw_args = copied.get("args")
    if isinstance(raw_args, Mapping):
        copied["args"] = MappingProxyType(dict(raw_args))
    return MappingProxyType(copied)


@dataclass(frozen=True, slots=True)
class ReplanContext:
    """Read-only attempt view; recovery accounting lives in task state."""

    task: str
    current_step: Mapping[str, Any]
    tool_history: Sequence[Mapping[str, Any]] = ()
    failure: FailureFact = field(default_factory=FailureFact.unknown)
    last_tool_result: ToolResult | None = None
    # Diagnostic compatibility field. It is never read for policy.
    last_exception: str | None = None
    budget_remaining: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "current_step", _readonly_step(self.current_step))
        object.__setattr__(self, "tool_history", tuple(self.tool_history))
        if not isinstance(self.failure, FailureFact):
            raise TypeError("ReplanContext.failure must be a FailureFact")


@dataclass
class ReplanAction:
    # Recovery actions enter the execution core as the same typed Plan used
    # by normal planning.  List-shaped construction remains a compatibility
    # input at this model boundary.
    steps: Plan = field(default_factory=Plan)
    source: str = ""
    reason: str = ""
    planning_view: Any = None

    def __post_init__(self) -> None:
        if isinstance(self.steps, Plan):
            return
        object.__setattr__(self, "steps", Plan.from_raw(self.steps))


__all__ = [
    "ErrorCategory",
    "ReplanAction",
    "ReplanContext",
    "category_for_failure",
]
