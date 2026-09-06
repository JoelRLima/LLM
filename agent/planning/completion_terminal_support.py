"""Terminal-failure evidence helpers for task completion."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.execution_state import StepStatus
from agent.runtime.failure_policy import (
    FailureClass,
    classify_failure,
    local_failure_permitted,
    unrecovered_local_failure_observations,
)
from agent.runtime.outcome_taxonomy import NON_SUCCESS_STATUSES
from agent.tools.result_adapter import result_status


def _later_recovery(state: Any, index: int, entry: Mapping[str, Any]) -> bool:
    checker = getattr(state, "_later_recovery", None)
    return bool(checker(index, entry)) if callable(checker) else False


def _step_failure_requires_terminal(state: Any, record: Any, permits: Any) -> bool:
    relevant = [
        (index, entry)
        for index, entry in enumerate(getattr(state, "tool_history", ()) or ())
        if isinstance(entry, Mapping)
        and str(entry.get("step_id") or "") == str(getattr(record, "step_id", ""))
        and isinstance(entry.get("result"), Mapping)
    ]
    if not relevant:
        return True
    return any(
        classify_failure(entry["result"]) is FailureClass.LOCAL
        and not _later_recovery(state, index, entry)
        and not (callable(permits) and permits(index + 1))
        for index, entry in relevant
    )


def _local_failure_requires_terminal(orchestrator: Any, *, include_invocation_history: bool) -> bool:
    state = orchestrator.agent_state
    semantics = getattr(state, "task_semantics", None)
    permits = getattr(semantics, "failure_observation_permitted", None)
    if include_invocation_history and any(
        not permitted for _, _, permitted in unrecovered_local_failure_observations(state)
    ):
        return True
    records = getattr(state, "step_records", {})
    if not isinstance(records, dict):
        return False
    failed_statuses = {StepStatus.FAILED, StepStatus.BLOCKED, StepStatus.UNVERIFIED}
    return any(
        _step_failure_requires_terminal(state, record, permits)
        for record in records.values()
        if getattr(record, "status", None) in failed_statuses
    )


def _hard_failure_from_state(
    orchestrator: Any,
    state: Any,
    hard_checker: Any,
    failure_checker: Any,
    include_invocation_history: bool,
) -> bool:
    task_failed = bool(getattr(orchestrator, "_task_failed", False))
    if callable(hard_checker):
        try:
            return bool(hard_checker(task_failed=task_failed))
        except TypeError:
            return bool(hard_checker())
    if callable(failure_checker):
        try:
            return bool(
                failure_checker(
                    task_failed=task_failed,
                    include_invocation_history=include_invocation_history,
                    hard_only=True,
                )
            )
        except TypeError:
            return False
    return task_failed


def _task_failed_without_fallback(state: Any, orchestrator: Any, failure_checker: Any, include_invocation_history: bool) -> bool:
    if not getattr(orchestrator, "_task_failed", False) or not callable(failure_checker):
        return False
    try:
        unresolved = failure_checker(
            task_failed=True,
            include_invocation_history=include_invocation_history,
            hard_only=False,
        )
    except TypeError:
        unresolved = True
    return bool(unresolved and not local_failure_permitted(state))


def _last_result_is_terminal(
    orchestrator: Any,
    state: Any,
    hard_checker: Any,
) -> bool:
    result = getattr(state, "last_result", None)
    if not isinstance(result, Mapping):
        return bool(getattr(orchestrator, "_task_failed", False)) and not callable(hard_checker)
    classification = classify_failure(result)
    if classification is FailureClass.HARD:
        return True
    if classification is FailureClass.LOCAL:
        local_failure = _local_failure_requires_terminal(orchestrator, include_invocation_history=True)
        return local_failure or not local_failure_permitted(state)
    return result_status(result) in NON_SUCCESS_STATUSES

__all__ = [
    "_hard_failure_from_state",
    "_last_result_is_terminal",
    "_local_failure_requires_terminal",
    "_step_failure_requires_terminal",
    "_task_failed_without_fallback",
]
