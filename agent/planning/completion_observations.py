"""Canonical observation/effect projections used by task completion."""

from __future__ import annotations

import json
from typing import Any

from agent.execution_state import StepStatus
from agent.planning.operational_constants import (
    TERMINAL_FAILURE_STATUSES,
    WRITE_CAPABILITIES,
)
from agent.reporting.operational_outcome import (
    artifact_metadata,
    metadata_is_persisted_mutation,
    project_operational_outcome,
)


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
    for item in getattr(state, "tool_history", ()) or ():
        if not isinstance(item, dict) or not isinstance(item.get("result"), dict):
            continue
        result = item["result"]
        if result.get("executed") is not True:
            continue
        capabilities = tool_capabilities(orchestrator, str(item.get("tool", "")))
        if capabilities & WRITE_CAPABILITIES and any(
            metadata_is_persisted_mutation(metadata)
            for metadata in artifact_metadata(result)
        ):
            state.record_executed_effect("write")


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
            result.get("executed") is not True
            or result.get("status") != "succeeded"
            or "data" not in result
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


def terminal_failure(orchestrator: Any) -> bool:
    if getattr(orchestrator, "_task_failed", False):
        return True
    records = getattr(orchestrator.agent_state, "step_records", {})
    if isinstance(records, dict) and any(
        getattr(record, "status", None)
        in {StepStatus.FAILED, StepStatus.BLOCKED, StepStatus.UNVERIFIED}
        for record in records.values()
    ):
        return True
    result = getattr(orchestrator.agent_state, "last_result", None)
    if not isinstance(result, dict):
        return False
    return str(result.get("status") or "") in TERMINAL_FAILURE_STATUSES
