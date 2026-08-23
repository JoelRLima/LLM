"""Canonical observation/effect projections used by task completion."""

from __future__ import annotations

import json
from typing import Any

from agent.execution_state import StepStatus
from agent.planning.failure_policy import (
    FailureClass,
    classify_failure,
    local_failure_permitted,
    unrecovered_local_failure_observations,
)
from agent.planning.operational_constants import TERMINAL_FAILURE_STATUSES
from agent.planning.task_semantics_effects import (
    effect_observation_proves_terminal,
)
from agent.planning.task_semantics_effects import (
    tool_capabilities as _tool_capabilities,
)
from agent.planning.task_semantics_types import ObligationStatus
from agent.reporting.operational_outcome import project_operational_outcome

tool_capabilities = _tool_capabilities


def refresh_executed_effects(orchestrator: Any) -> None:
    state = orchestrator.agent_state
    for history_index, item in enumerate(getattr(state, "tool_history", ()) or (), start=1):
        if not isinstance(item, dict) or not isinstance(item.get("result"), dict):
            continue
        if effect_observation_proves_terminal(
            orchestrator,
            ObligationStatus.SATISFIED,
            item,
        ):
            semantics = getattr(state, "task_semantics", None)
            register = getattr(semantics, "register_observation", None)
            if callable(register):
                register(
                    str(item.get("tool", "")),
                    item["result"],
                    evidence_ref=history_index,
                    args=item.get("args") if isinstance(item.get("args"), dict) else {},
                )
            state.record_executed_effect(
                "write",
                evidence_ref=history_index,
                effect_authority=orchestrator,
            )


def eligible_waiver_observations(
    orchestrator: Any,
) -> list[tuple[int, dict[str, Any]]]:
    eligible: list[tuple[int, dict[str, Any]]] = []
    for index, item in enumerate(orchestrator.agent_state.tool_history, start=1):
        if not isinstance(item, dict):
            continue
        result = item.get("result")
        if not isinstance(result, dict):
            continue
        if effect_observation_proves_terminal(orchestrator, ObligationStatus.WAIVED, item):
            eligible.append((index, item))
    return eligible


def observation_references(orchestrator: Any) -> str:
    return "\n".join(
        f"{index}: tool={json.dumps(str(item.get('tool', '')), ensure_ascii=False)}"
        for index, item in eligible_waiver_observations(orchestrator)
    )


def publish_outcome(orchestrator: Any) -> None:
    projection = project_operational_outcome(
        orchestrator.agent_state,
        task_failed=bool(getattr(orchestrator, "_task_failed", False)),
        cancelled=bool(getattr(orchestrator, "_cancelled", False)),
    ).debug_projection()
    events = getattr(orchestrator.agent_state, "events", None) or ()
    emit = getattr(orchestrator, "_emit", None)
    if not callable(emit):
        return
    if any(
        isinstance(event, dict)
        and event.get("type") == "task_outcome"
        and event.get("data") == projection
        for event in events
    ):
        return
    emit("task_outcome", projection)


def _later_recovery(state: Any, index: int, entry: dict[str, Any]) -> bool:
    checker = getattr(state, "_later_recovery", None)
    return bool(checker(index, entry)) if callable(checker) else False


def _step_failure_requires_terminal(state: Any, record: Any, permits: Any) -> bool:
    relevant = [
        (index, entry)
        for index, entry in enumerate(getattr(state, "tool_history", ()) or ())
        if isinstance(entry, dict)
        and str(entry.get("step_id") or "") == str(getattr(record, "step_id", ""))
        and isinstance(entry.get("result"), dict)
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
    if not isinstance(result, dict):
        return bool(getattr(orchestrator, "_task_failed", False)) and not callable(hard_checker)
    classification = classify_failure(result)
    if classification is FailureClass.HARD:
        return True
    if classification is FailureClass.LOCAL:
        local_failure = _local_failure_requires_terminal(orchestrator, include_invocation_history=True)
        return local_failure or not local_failure_permitted(state)
    return str(result.get("status") or "") in TERMINAL_FAILURE_STATUSES


def terminal_failure(
    orchestrator: Any,
    *,
    include_invocation_history: bool = False,
    hard_only: bool = False,
) -> bool:
    state = orchestrator.agent_state
    hard_checker = getattr(state, "has_unrecovered_hard_task_failures", None)
    failure_checker = getattr(state, "has_unrecovered_task_failures", None)
    if _hard_failure_from_state(
        orchestrator,
        state,
        hard_checker,
        failure_checker,
        include_invocation_history,
    ):
        return True
    if hard_only:
        return False
    if _task_failed_without_fallback(state, orchestrator, failure_checker, include_invocation_history):
        return True
    if _local_failure_requires_terminal(
        orchestrator,
        include_invocation_history=include_invocation_history,
    ):
        return True
    return _last_result_is_terminal(orchestrator, state, hard_checker)
