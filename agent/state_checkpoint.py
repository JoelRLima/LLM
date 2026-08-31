"""Checkpoint serialization for task-level progression fields."""

from __future__ import annotations

from typing import Any

from agent.planning.task_completion_types import CompletionDisposition
from agent.planning.task_semantics import TaskSemantics, TaskSemanticsError
from agent.runtime.outcome_taxonomy import NON_SUCCESS_STATUSES
from agent.state_checkpoint_counters import restore_counters as _restore_counters

_VALID_TERMINAL_DISPOSITIONS = (
    frozenset(item.value for item in CompletionDisposition) | NON_SUCCESS_STATUSES
)


def progression_checkpoint(state: Any) -> dict[str, Any]:
    semantics = getattr(state, "task_semantics", None)
    objective = getattr(state, "objective", None)
    semantic_checkpoint = (
        semantics.to_checkpoint_dict()
        if isinstance(semantics, TaskSemantics)
        and (
            objective == semantics.objective
            or (objective is None and semantics.objective == "")
        )
        else None
    )
    return {
        "requested_effects": state.requested_effects,
        "executed_effects": state.executed_effects,
        "waived_effects": state.waived_effects,
        "prohibited_effects": getattr(state, "prohibited_effects", []),
        "task_semantics": semantic_checkpoint,
        "continuation_attempts": state.continuation_attempts,
        "replan_counts": dict(getattr(state, "replan_counts", {}) or {}),
        "reasoning_turns_used": state.reasoning_turns_used,
        "recovery_budget": (
            state.recovery_budget.to_checkpoint_dict()
            if callable(getattr(getattr(state, "recovery_budget", None), "to_checkpoint_dict", None))
            else None
        ),
        "reasoning_last_history_count": state.reasoning_last_history_count,
        "reasoning_last_progress_token": state.reasoning_last_progress_token,
        "continue_after_plan": state.continue_after_plan,
        "terminal_disposition": state.terminal_disposition,
        "task_policy": (
            state.task_policy_state.to_checkpoint_dict()
            if callable(getattr(getattr(state, "task_policy_state", None), "to_checkpoint_dict", None))
            else None
        ),
        "hierarchical_lifecycle": dict(
            getattr(state, "hierarchical_lifecycle", {"status": "inactive"})
        ),
    }


def restore_progression(state: Any, data: dict[str, Any]) -> None:
    _restore_semantics(state, data)
    _restore_counters(state, data)
    _restore_task_policy(state, data)
    _restore_hierarchical_lifecycle(state, data)
    _restore_terminal(state, data)


def _restore_task_policy(state: Any, data: dict[str, Any]) -> None:
    policy_state = getattr(state, "task_policy_state", None)
    if policy_state is None:
        return
    raw = data.get("task_policy")
    if raw is None:
        policy_state.reset()
        return
    if not isinstance(raw, dict):
        raise ValueError("Checkpoint task policy state is invalid.")
    try:
        policy_state.restore_checkpoint(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("Checkpoint task policy state is invalid.") from exc


def _restore_hierarchical_lifecycle(state: Any, data: dict[str, Any]) -> None:
    raw = data.get("hierarchical_lifecycle", {"status": "inactive"})
    if not isinstance(raw, dict):
        raise ValueError("Checkpoint hierarchical lifecycle is invalid.")
    status = raw.get("status", "inactive")
    if status not in {"inactive", "running", "completed"}:
        raise ValueError("Checkpoint hierarchical lifecycle status is invalid.")
    state.hierarchical_lifecycle = dict(raw)


def _restore_legacy_semantics(state: Any, data: dict[str, Any]) -> None:
    legacy_keys = ("requested_effects", "executed_effects", "waived_effects")
    if any(key not in data for key in legacy_keys):
        raise ValueError(
            "Checkpoint lacks canonical task semantics and has no unambiguous legacy state."
        )
    for key in legacy_keys + ("prohibited_effects",):
        if key in data and not _string_list(data[key]):
            raise ValueError(f"Checkpoint field is not a string list: {key}.")
    state.set_task_semantics(
        TaskSemantics.from_legacy(
            str(getattr(state, "objective", None) or ""),
            data["requested_effects"],
            data["executed_effects"],
            data["waived_effects"],
            data.get("prohibited_effects") or [],
        )
    )


def _restore_semantics(state: Any, data: dict[str, Any]) -> None:
    raw_semantics = data.get("task_semantics")
    if isinstance(raw_semantics, dict) and hasattr(state, "set_task_semantics"):
        try:
            semantics = TaskSemantics.from_checkpoint_dict(raw_semantics)
        except (TaskSemanticsError, TypeError, AttributeError) as exc:
            raise ValueError("Checkpoint contains invalid task semantics.") from exc
        objective = getattr(state, "objective", None)
        if objective is not None and semantics.objective != objective:
            raise ValueError("Checkpoint task intent does not match its objective.")
        state.set_task_semantics(semantics)
    elif hasattr(state, "set_task_semantics"):
        _restore_legacy_semantics(state, data)
    else:
        state.requested_effects = [str(item) for item in (data.get("requested_effects") or [])]
        state.executed_effects = [str(item) for item in (data.get("executed_effects") or [])]
        state.waived_effects = [str(item) for item in (data.get("waived_effects") or [])]


def _restore_terminal(state: Any, data: dict[str, Any]) -> None:
    terminal = data.get("terminal_disposition")
    if terminal is None:
        setter = getattr(state, "set_terminal_disposition", None)
        if callable(setter):
            setter(None)
        else:
            state.terminal_disposition = None
        return
    if not isinstance(terminal, str):
        raise ValueError("Checkpoint terminal disposition is invalid.")
    normalized_terminal = terminal
    if normalized_terminal not in _VALID_TERMINAL_DISPOSITIONS:
        raise ValueError("Checkpoint contains an unsupported terminal disposition.")
    setter = getattr(state, "set_terminal_disposition", None)
    if callable(setter):
        setter(normalized_terminal)
    else:
        state.terminal_disposition = normalized_terminal


def _string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)
