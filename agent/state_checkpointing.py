"""Strict checkpoint projection/restoration methods for AgentState."""

from __future__ import annotations

import json
from typing import Any, Dict, Mapping, cast

from agent.contracts import CheckpointData
from agent.execution_state import StepExecutionRecord
from agent.planning.plan_model import Plan, deserialize_plan, serialize_plan
from agent.state_checkpoint import progression_checkpoint, restore_progression
from agent.state_checkpoint_auxiliary import restore_auxiliary_state
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
from agent.task_definition.models import TaskDefinitionRef
from agent.tools.result_adapter import ensure_canonical_result, to_legacy_result


class StateCheckpointMixin:
    def to_checkpoint_dict(self: Any) -> CheckpointData:
        ensure_continuity = getattr(self, "ensure_continuity_for_current_run", None)
        if callable(ensure_continuity):
            ensure_continuity()
        memory_state = getattr(self.memory, "state", None)
        # The on-disk schema is a supported compatibility edge.  Keep live
        # state canonical and project only while constructing this snapshot.
        last_result = getattr(self, "last_result", None)
        checkpoint_last_result = (
            to_legacy_result(last_result) if last_result is not None else None
        )
        checkpoint_history = []
        for raw_entry in getattr(self, "tool_history", ()):
            entry = dict(raw_entry)
            if "result" in entry:
                entry["result"] = to_legacy_result(entry["result"])
            checkpoint_history.append(entry)
        task_definition_ref: TaskDefinitionRef | None = getattr(
            self, "task_definition_ref", None
        )
        raw: Dict[str, Any] = {
            "objective": self.objective,
            "root_task_id": getattr(self, "root_task_id", None),
            "plan": serialize_plan(self.plan) if isinstance(self.plan, Plan) else self.plan,
            "plan_identity": self.plan_identity,
            "plan_step": self.plan_step,
            "current_step_id": self.current_step_id,
            "step_records": [record.to_dict() for record in self.step_records.values()],
            "last_tool": self.last_tool,
            "last_args": self.last_args,
            "last_result": checkpoint_last_result,
            "tool_history": checkpoint_history,
            "execution_incidents": self.execution_incidents,
            "events": self.events,
            "conversation_history": self.conversation_history,
            "memory_state": memory_state,
            "persona": self.persona,
            "persona_prompt": self.persona_prompt,
            **progression_checkpoint(self),
            "task_definition": (
                task_definition_ref.to_dict() if task_definition_ref is not None else None
            ),
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
        provisional._continuity_resume_pending = _resume_continuity_supported(provisional)
        _validate_restored_cross_fields(provisional)
        _publish_provisional_state(self, provisional)


def _restore_objective(state: Any, data: Mapping[str, Any]) -> str | None:
    objective = data.get("objective", state.objective)
    if objective is not None and not isinstance(objective, str):
        raise ValueError("Checkpoint objective must be textual or null.")
    return objective


def _restore_plan(state: Any, data: Mapping[str, Any]) -> None:
    raw_plan = data.get("plan")
    if raw_plan is None:
        raw_plan = state.plan if isinstance(state.plan, Plan) else []
    if isinstance(raw_plan, Plan):
        restored_plan = raw_plan
    else:
        if not isinstance(raw_plan, list) or any(not isinstance(step, Mapping) for step in raw_plan):
            raise ValueError("Checkpoint plan is structurally invalid.")
        if _contains_retired_tool_alias(raw_plan):
            raise ValueError("W7_RETIRED_TOOL_ALIAS:git")
        try:
            restored_plan = deserialize_plan(
                raw_plan,
                new_step_id=getattr(state, "_new_step_id", None),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("Checkpoint plan is structurally invalid.") from exc
    state.set_plan(restored_plan)
    identity = data.get("plan_identity")
    if state.plan and isinstance(identity, str) and identity.strip():
        state.plan_identity = identity
    elif identity is not None and state.plan:
        raise ValueError("Checkpoint plan identity is invalid.")
    cursor = data.get("plan_step", state.plan_step)
    if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0:
        raise ValueError("Checkpoint plan cursor is invalid.")
    state.plan_step = cursor


def _contains_retired_tool_alias(value: Any) -> bool:
    if isinstance(value, Mapping):
        if value.get("tool") == "git":
            return True
        return any(_contains_retired_tool_alias(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_retired_tool_alias(item) for item in value)
    return False


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
    state.last_result = (
        ensure_canonical_result(last_result)
        if isinstance(last_result, Mapping)
        else last_result
    )


def _restore_auxiliary_state(state: Any, data: Mapping[str, Any]) -> None:
    restore_auxiliary_state(state, data)


def _resume_continuity_supported(state: Any) -> bool:
    if getattr(state, "terminal_disposition", None) is not None:
        return False
    lifecycle = getattr(state, "hierarchical_lifecycle", {})
    return not (isinstance(lifecycle, Mapping) and lifecycle.get("status") == "running")
