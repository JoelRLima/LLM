"""Planning compatibility adapter for the runtime failure policy."""

from __future__ import annotations

from agent.runtime.failure_policy import (
    HARD_FAILURE_CODES,
    HARD_FAILURE_STATUSES,
    LOCAL_FAILURE_STATUSES,
    FailureClass,
    classify_failure,
    is_hard_failure,
    is_local_failure,
    local_failure_permitted,
    unrecovered_local_failure_observations,
)

__all__ = [
    "FailureClass",
    "HARD_FAILURE_CODES",
    "HARD_FAILURE_STATUSES",
    "LOCAL_FAILURE_STATUSES",
    "classify_failure",
    "is_hard_failure",
    "is_local_failure",
    "local_failure_permitted",
    "unrecovered_local_failure_observations",
]
