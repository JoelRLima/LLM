"""Auxiliary task and session state restoration for checkpoints."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.task_definition.models import TaskDefinitionRef


def restore_auxiliary_state(state: Any, data: Mapping[str, Any]) -> None:
    task_definition_ref = _task_definition_ref(data)
    values = _auxiliary_values(state, data)
    _validate_auxiliary_values(values)
    _restore_execution_incidents(state, values["incidents"])
    _apply_auxiliary_state(state, task_definition_ref, values)


def _task_definition_ref(data: Mapping[str, Any]) -> TaskDefinitionRef | None:
    raw = data.get("task_definition")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("Checkpoint task definition binding is invalid.")
    try:
        return TaskDefinitionRef.from_dict(dict(raw))
    except (TypeError, ValueError) as exc:
        raise ValueError("Checkpoint task definition binding is invalid.") from exc


def _auxiliary_values(state: Any, data: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "root_task_id": data.get("root_task_id"),
        "events": data.get("events", state.events) or [],
        "history": data.get("conversation_history", state.conversation_history) or [],
        "persona": data.get("persona", state.persona),
        "persona_prompt": data.get("persona_prompt", state.persona_prompt),
        "memory_state": data.get("memory_state"),
        "incidents": data.get(
            "execution_incidents", getattr(state, "execution_incidents", [])
        ),
        "budget": data.get("budget"),
    }


def _validate_auxiliary_values(values: dict[str, Any]) -> None:
    _validate_collections(values)
    _validate_identities(values)
    _validate_text_fields(values)
    _validate_mappings(values)


def _validate_collections(values: dict[str, Any]) -> None:
    if not isinstance(values["events"], list):
        raise ValueError("Checkpoint events are invalid.")
    if not isinstance(values["history"], list) or any(
        not isinstance(entry, Mapping) for entry in values["history"]
    ):
        raise ValueError("Checkpoint conversation history is invalid.")


def _validate_identities(values: dict[str, Any]) -> None:
    root_task_id = values["root_task_id"]
    if root_task_id is not None and (
        not isinstance(root_task_id, str) or not root_task_id.strip()
    ):
        raise ValueError("Checkpoint root task identity is invalid.")


def _validate_text_fields(values: dict[str, Any]) -> None:
    if values["persona"] is not None and not isinstance(values["persona"], str):
        raise ValueError("Checkpoint persona is invalid.")
    if values["persona_prompt"] is not None and not isinstance(values["persona_prompt"], str):
        raise ValueError("Checkpoint persona prompt is invalid.")


def _validate_mappings(values: dict[str, Any]) -> None:
    if values["budget"] is not None and not isinstance(values["budget"], Mapping):
        raise ValueError("Checkpoint budget snapshot is invalid.")
    if values["memory_state"] is not None and not isinstance(values["memory_state"], Mapping):
        raise ValueError("Checkpoint memory state is invalid.")


def _restore_execution_incidents(state: Any, incidents: Any) -> None:
    restore_incidents = getattr(state, "restore_execution_incidents", None)
    if not callable(restore_incidents):
        raise ValueError("Checkpoint incident owner is unavailable.")
    try:
        restore_incidents(incidents)
    except (TypeError, ValueError) as exc:
        raise ValueError("Checkpoint execution incident journal is invalid.") from exc


def _apply_auxiliary_state(
    state: Any,
    task_definition_ref: TaskDefinitionRef | None,
    values: dict[str, Any],
) -> None:
    root_task_id = values["root_task_id"]
    if task_definition_ref is not None and task_definition_ref.task_id != root_task_id:
        raise ValueError("Checkpoint task definition does not match root task identity.")
    state.root_task_id = root_task_id
    state.task_definition_ref = task_definition_ref
    state.events = values["events"]
    state.conversation_history = [dict(entry) for entry in values["history"]]
    state.persona = values["persona"]
    state.persona_prompt = values["persona_prompt"]
    if state.budget_ledger is not None and isinstance(values["budget"], Mapping):
        state.budget_ledger.restore_snapshot(values["budget"])
    if values["memory_state"] is not None and hasattr(state.memory, "state"):
        state.memory.state = dict(values["memory_state"])
