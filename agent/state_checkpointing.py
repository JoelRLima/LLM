"""Strict checkpoint projection/restoration methods for AgentState."""

from __future__ import annotations

import json
from typing import Any, Dict, Mapping, cast

from agent.contracts import CheckpointData
from agent.execution_state import StepExecutionRecord
from agent.state_checkpoint import progression_checkpoint, restore_progression
from agent.state_checkpoint_history import restore_histories as _restore_histories
from agent.state_checkpoint_restore import (
    provisional_state as _provisional_state,
)
from agent.state_checkpoint_restore import (
    publish_provisional_state as _publish_provisional_state,
)
from agent.state_checkpoint_restore import (
    validate_restored_cross_fields as _validate_restored_cross_fields,
)


class StateCheckpointMixin:
    def to_checkpoint_dict(self: Any) -> CheckpointData:
        memory_state = getattr(self.memory, "state", None)
        raw: Dict[str, Any] = {
            "objective": self.objective,
            "plan": self.plan,
            "plan_identity": self.plan_identity,
            "plan_step": self.plan_step,
            "current_step_id": self.current_step_id,
            "step_records": [record.to_dict() for record in self.step_records.values()],
            "last_tool": self.last_tool,
            "last_args": self.last_args,
            "last_result": self.last_result,
            "tool_history": self.tool_history,
            "execution_incidents": self.execution_incidents,
            "events": self.events,
            "conversation_history": self.conversation_history,
            "memory_state": memory_state,
            "persona": self.persona,
            "persona_prompt": self.persona_prompt,
            **progression_checkpoint(self),
        }
        if self.budget_ledger is not None:
            raw["budget"] = self.budget_ledger.snapshot().to_dict()
        return cast(CheckpointData, json.loads(json.dumps(raw, ensure_ascii=False, default=str)))

    def from_checkpoint_dict(
        self: Any,
        data: Mapping[str, Any],
        retry_failed: bool = False,
        retry_skipped: bool = False,
        *,
        effect_authority: Any = None,
        admission_authority: Any = None,
    ) -> None:
        if not isinstance(data, Mapping):
            raise ValueError("Checkpoint root must be an object.")
        provisional = _provisional_state(self)
        provisional.objective = _restore_objective(provisional, data)
        _restore_plan(provisional, data)
        restore_progression(provisional, dict(data))
        _restore_step_records(provisional, data)
        _restore_last_result(provisional, data)
        _restore_histories(
            provisional,
            data,
            effect_authority=effect_authority,
            admission_authority=admission_authority,
        )
        _restore_auxiliary_state(provisional, data)
        provisional.prepare_for_resume(
            retry_failed=retry_failed,
            retry_skipped=retry_skipped,
        )
        _validate_restored_cross_fields(provisional)
        _publish_provisional_state(self, provisional)


def _restore_objective(state: Any, data: Mapping[str, Any]) -> str | None:
    objective = data.get("objective", state.objective)
    if objective is not None and not isinstance(objective, str):
        raise ValueError("Checkpoint objective must be textual or null.")
    return objective


def _restore_plan(state: Any, data: Mapping[str, Any]) -> None:
    raw_plan = data.get("plan", state.plan) or []
    if not isinstance(raw_plan, list) or any(not isinstance(step, Mapping) for step in raw_plan):
        raise ValueError("Checkpoint plan is structurally invalid.")
    state.set_plan(raw_plan)
    identity = data.get("plan_identity")
    if state.plan and isinstance(identity, str) and identity.strip():
        state.plan_identity = identity
    elif identity is not None and state.plan:
        raise ValueError("Checkpoint plan identity is invalid.")
    cursor = data.get("plan_step", state.plan_step)
    if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0:
        raise ValueError("Checkpoint plan cursor is invalid.")
    state.plan_step = cursor


def _restore_step_records(state: Any, data: Mapping[str, Any]) -> None:
    raw_records = data.get("step_records")
    if not isinstance(raw_records, list):
        raise ValueError("Checkpoint step records are invalid.")
    for raw_record in raw_records:
        if not isinstance(raw_record, Mapping):
            raise ValueError("Checkpoint step record is invalid.")
        record = StepExecutionRecord.from_dict(dict(raw_record))
        if record.step_id not in state.step_records:
            raise ValueError("Checkpoint step record does not belong to the plan.")
        state.step_records[record.step_id] = record


def _restore_last_result(state: Any, data: Mapping[str, Any]) -> None:
    current_step_id = data.get("current_step_id")
    if current_step_id is not None and not isinstance(current_step_id, str):
        raise ValueError("Checkpoint current step identity is invalid.")
    if current_step_id is not None and current_step_id not in state.step_records:
        raise ValueError("Checkpoint current step is not present in the plan.")
    state.current_step_id = current_step_id
    last_tool = data.get("last_tool", state.last_tool)
    last_args = data.get("last_args", state.last_args)
    last_result = data.get("last_result", state.last_result)
    if last_tool is not None and not isinstance(last_tool, str):
        raise ValueError("Checkpoint last tool is invalid.")
    if last_args is not None and not isinstance(last_args, Mapping):
        raise ValueError("Checkpoint last arguments are invalid.")
    if last_result is not None and not isinstance(last_result, Mapping):
        raise ValueError("Checkpoint last result is invalid.")
    state.last_tool = last_tool
    state.last_args = dict(last_args) if isinstance(last_args, Mapping) else last_args
    state.last_result = dict(last_result) if isinstance(last_result, Mapping) else last_result


def _restore_auxiliary_state(state: Any, data: Mapping[str, Any]) -> None:
    events = data.get("events", state.events) or []
    history = data.get("conversation_history", state.conversation_history) or []
    persona = data.get("persona", state.persona)
    persona_prompt = data.get("persona_prompt", state.persona_prompt)
    memory_state = data.get("memory_state")
    incidents = data.get("execution_incidents", getattr(state, "execution_incidents", []))
    budget = data.get("budget")
    if not isinstance(events, list):
        raise ValueError("Checkpoint events are invalid.")
    if not isinstance(history, list) or any(not isinstance(entry, Mapping) for entry in history):
        raise ValueError("Checkpoint conversation history is invalid.")
    if persona is not None and not isinstance(persona, str):
        raise ValueError("Checkpoint persona is invalid.")
    if persona_prompt is not None and not isinstance(persona_prompt, str):
        raise ValueError("Checkpoint persona prompt is invalid.")
    if budget is not None and not isinstance(budget, Mapping):
        raise ValueError("Checkpoint budget snapshot is invalid.")
    if memory_state is not None and not isinstance(memory_state, Mapping):
        raise ValueError("Checkpoint memory state is invalid.")
    _restore_execution_incidents(state, incidents)
    state.events = events
    state.conversation_history = [dict(entry) for entry in history]
    state.persona = persona
    state.persona_prompt = persona_prompt
    if state.budget_ledger is not None and isinstance(budget, Mapping):
        state.budget_ledger.restore_snapshot(budget)
    if memory_state is not None and hasattr(state.memory, "state"):
        state.memory.state = dict(memory_state)


def _restore_execution_incidents(state: Any, incidents: Any) -> None:
    restore_incidents = getattr(state, "restore_execution_incidents", None)
    if not callable(restore_incidents):
        raise ValueError("Checkpoint incident owner is unavailable.")
    try:
        restore_incidents(incidents)
    except (TypeError, ValueError) as exc:
        raise ValueError("Checkpoint execution incident journal is invalid.") from exc
