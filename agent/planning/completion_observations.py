"""Canonical observation/effect projections used by task completion."""

from __future__ import annotations

import json
from typing import Any

from agent.execution_state import StepStatus
from agent.planning.operational_constants import (
    TERMINAL_FAILURE_STATUSES,
    WRITE_CAPABILITIES,
)
from agent.reporting.observation_evidence import (
    project_artifact_evidence,
    result_executed,
    result_has_data,
    result_is_successful,
)
from agent.reporting.operational_outcome import project_operational_outcome


def tool_capabilities(orchestrator: Any, tool_name: str) -> frozenset[str]:
    registry = getattr(orchestrator, "tool_registry", None)
    if registry is None:
        return frozenset()
    try:
        descriptor = registry.descriptor(tool_name)
    except KeyError:
        return frozenset()
    return frozenset(str(item) for item in descriptor.capabilities)


def refresh_executed_effects(orchestrator: Any) -> None:
    state = orchestrator.agent_state
    for history_index, item in enumerate(getattr(state, "tool_history", ()) or (), start=1):
        if not isinstance(item, dict) or not isinstance(item.get("result"), dict):
            continue
        result = item["result"]
        if result_executed(result) is not True:
            continue
        capabilities = tool_capabilities(orchestrator, str(item.get("tool", "")))
        if capabilities & WRITE_CAPABILITIES and project_artifact_evidence(result).persisted_mutation:
            state.record_executed_effect("write", evidence_ref=history_index)


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
        if (
            result_executed(result) is not True
            or not result_is_successful(result)
            or not result_has_data(result)
        ):
            continue
        capabilities = tool_capabilities(orchestrator, str(item.get("tool", "")))
        if capabilities & WRITE_CAPABILITIES:
            continue
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


def terminal_failure(orchestrator: Any, *, include_invocation_history: bool = False) -> bool:
    state = orchestrator.agent_state
    failure_checker = getattr(state, "has_unrecovered_task_failures", None)
    if callable(failure_checker):
        try:
            failed = failure_checker(
                task_failed=bool(getattr(orchestrator, "_task_failed", False)),
                include_invocation_history=include_invocation_history,
            )
        except TypeError:
            failed = failure_checker(
                task_failed=bool(getattr(orchestrator, "_task_failed", False))
            )
        if failed:
            return True
    elif getattr(orchestrator, "_task_failed", False):
        return True
    records = getattr(orchestrator.agent_state, "step_records", {})
    if not callable(failure_checker) and isinstance(records, dict) and any(
        getattr(record, "status", None)
        in {StepStatus.FAILED, StepStatus.BLOCKED, StepStatus.UNVERIFIED}
        for record in records.values()
    ):
        return True
    result = getattr(state, "last_result", None)
    if not isinstance(result, dict):
        return False
    return str(result.get("status") or "") in TERMINAL_FAILURE_STATUSES
