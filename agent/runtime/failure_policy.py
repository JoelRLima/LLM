"""Canonical hard-boundary versus local-attempt failure classification."""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Any

from agent.runtime.failures import FailureFact
from agent.runtime.outcome_taxonomy import HARD_FAILURE_CODES, HARD_FAILURE_STATUSES, LOCAL_FAILURE_STATUSES
from agent.tools.contracts import ToolResult
from agent.tools.result_adapter import ensure_canonical_result


class FailureClass(str, Enum):
    NONE = "none"
    LOCAL = "local"
    HARD = "hard"


def classify_failure(result: Any) -> FailureClass:
    """Classify observed failure facts without deciding task completion."""

    fact = failure_fact_for_result(result)
    if fact is None:
        return FailureClass.NONE
    if fact.hard or fact.status in HARD_FAILURE_STATUSES or fact.code in HARD_FAILURE_CODES:
        return FailureClass.HARD
    if fact.status in LOCAL_FAILURE_STATUSES:
        return FailureClass.LOCAL
    return FailureClass.NONE


def failure_fact_for_result(result: Any) -> FailureFact | None:
    """Normalize a typed result, with one explicit legacy compatibility edge."""

    if isinstance(result, ToolResult):
        return FailureFact.from_tool_result(result)
    if isinstance(result, Mapping):
        return FailureFact.from_tool_result(ensure_canonical_result(result))
    return None


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
    "failure_fact_for_result",
    "is_hard_failure",
    "is_local_failure",
    "local_failure_permitted",
    "unrecovered_local_failure_observations",
]
