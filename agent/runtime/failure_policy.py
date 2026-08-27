"""Canonical hard-boundary versus local-attempt failure classification."""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Any

from agent.runtime.outcome_taxonomy import (
    HARD_FAILURE_CODES,
    HARD_FAILURE_STATUSES,
    LOCAL_FAILURE_STATUSES,
    operational_status_for,
)


class FailureClass(str, Enum):
    NONE = "none"
    LOCAL = "local"
    HARD = "hard"


def classify_failure(result: Any) -> FailureClass:
    """Classify observed failure facts without deciding task completion."""

    if not isinstance(result, Mapping):
        return FailureClass.NONE
    status = operational_status_for(result.get("status"))
    code = str(result.get("error_code") or "")
    if status in HARD_FAILURE_STATUSES or code in HARD_FAILURE_CODES:
        return FailureClass.HARD
    if status in LOCAL_FAILURE_STATUSES or result.get("ok") is False:
        return FailureClass.LOCAL
    return FailureClass.NONE


def is_hard_failure(result: Any) -> bool:
    return classify_failure(result) is FailureClass.HARD


def is_local_failure(result: Any) -> bool:
    return classify_failure(result) is FailureClass.LOCAL


def unrecovered_local_failure_observations(
    state: Any,
) -> tuple[tuple[int, Mapping[str, Any], bool], ...]:
    """Project local failures and whether exact fallback permits each one."""

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
    "LOCAL_FAILURE_STATUSES",
    "classify_failure",
    "is_hard_failure",
    "is_local_failure",
    "local_failure_permitted",
    "unrecovered_local_failure_observations",
]
