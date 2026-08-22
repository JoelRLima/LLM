"""Canonical hard-boundary versus local-attempt failure policy."""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping


class FailureClass(str, Enum):
    NONE = "none"
    LOCAL = "local"
    HARD = "hard"


HARD_FAILURE_STATUSES = frozenset(
    {"blocked", "cancelled", "permission_denied", "protocol_error", "unverified"}
)
HARD_FAILURE_CODES = frozenset(
    {
        "APPLICATION_AUTHORITY_DENIED",
        "APPLICATION_AUTHORITY_MISSING",
        "APPROVAL_DENIED",
        "APPROVAL_FAILED",
        "APPROVAL_REQUIRED",
        "AUTH_DENIED",
        "AUTH_REQUIRED",
        "AUTHORITY_REQUIRED",
        "CANCELLED",
        "CHECKPOINT_INVALID_TERMINAL_DISPOSITION",
        "DUPLICATE_INVOCATION_ID",
        "EXECUTION_ABORTED",
        "INVALID_ARGUMENTS",
        "INVALID_RESULT",
        "INVOCATION_ID_MISMATCH",
        "ORIGIN_MISMATCH",
        "PERMISSION_DENIED",
        "PROVIDER_FAILED",
        "RUNTIME_MISMATCH",
        "SAFETY_BLOCK",
        "TASK_AUTHORITY_DENIED",
        "TASK_AUTHORITY_MISSING",
        "TASK_BUDGET_EXHAUSTED",
        "TASK_CLEANUP_FAILURE",
        "TASK_COST_LIMIT_REACHED",
        "UNRESOLVED_SYMBOLIC_ARGUMENT",
        "WATCHDOG_TIMEOUT",
        "WORKSPACE_GRANT_DENIED",
        "prepared_invocation_stale",
        "reasoning_boundary_blocked",
        "unresolved_symbolic_argument",
    }
)


def classify_failure(result: Any) -> FailureClass:
    """Classify observed failure facts without deciding task completion."""

    if not isinstance(result, Mapping):
        return FailureClass.NONE
    status = str(result.get("status") or "")
    code = str(result.get("error_code") or "")
    if status in HARD_FAILURE_STATUSES or code in HARD_FAILURE_CODES:
        return FailureClass.HARD
    if status in {"failed", "timed_out", "unavailable"} or result.get("ok") is False:
        return FailureClass.LOCAL
    return FailureClass.NONE


def is_hard_failure(result: Any) -> bool:
    return classify_failure(result) is FailureClass.HARD


def is_local_failure(result: Any) -> bool:
    return classify_failure(result) is FailureClass.LOCAL


def unrecovered_local_failure_observations(
    state: Any,
) -> tuple[tuple[int, Mapping[str, Any], bool], ...]:
    """Project unrecovered local failures and whether semantics permits each one.

    The evidence reference is the one-based position used by the canonical
    task-semantic catalog.  This helper classifies observations only; it never
    changes task or terminal state.
    """

    semantics = getattr(state, "task_semantics", None)
    permits = getattr(semantics, "failure_observation_permitted", None)
    history = getattr(state, "tool_history", ()) or ()
    projected: list[tuple[int, Mapping[str, Any], bool]] = []
    recovery_checker = getattr(state, "_later_recovery", None)
    for index, entry in enumerate(history):
        if not isinstance(entry, Mapping):
            continue
        result = entry.get("result")
        if classify_failure(result) is not FailureClass.LOCAL:
            continue
        if callable(recovery_checker):
            try:
                if recovery_checker(index, entry):
                    continue
            except Exception:
                pass
        permitted = False
        if callable(permits):
            try:
                permitted = bool(permits(index + 1))
            except Exception:
                permitted = False
        projected.append((index + 1, entry, permitted))
    return tuple(projected)


def local_failure_permitted(state: Any) -> bool:
    """Return true only when every unrecovered local failure has exact fallback."""

    observations = unrecovered_local_failure_observations(state)
    return bool(observations) and all(permitted for _, _, permitted in observations)


__all__ = [
    "FailureClass",
    "HARD_FAILURE_CODES",
    "HARD_FAILURE_STATUSES",
    "classify_failure",
    "is_hard_failure",
    "is_local_failure",
    "local_failure_permitted",
    "unrecovered_local_failure_observations",
]
