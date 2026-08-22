"""Checkpoint serialization for task-level progression fields."""

from __future__ import annotations

from typing import Any

from agent.planning.task_semantics import TaskSemantics, TaskSemanticsError
from agent.reporting.operational_outcome import PUBLIC_TERMINAL_STATUSES

_VALID_TERMINAL_DISPOSITIONS = frozenset({"complete", "block", "fail"}) | (
    PUBLIC_TERMINAL_STATUSES - {"succeeded"}
)


def progression_checkpoint(state: Any) -> dict[str, Any]:
    semantics = getattr(state, "task_semantics", None)
    semantic_checkpoint = (
        semantics.to_checkpoint_dict()
        if isinstance(semantics, TaskSemantics)
        and getattr(state, "objective", None) == semantics.objective
        else None
    )
    return {
        "requested_effects": state.requested_effects,
        "executed_effects": state.executed_effects,
        "waived_effects": state.waived_effects,
        "prohibited_effects": getattr(state, "prohibited_effects", []),
        "task_semantics": semantic_checkpoint,
        "continuation_attempts": state.continuation_attempts,
        "reasoning_turns_used": state.reasoning_turns_used,
        "reasoning_last_history_count": state.reasoning_last_history_count,
        "reasoning_last_progress_token": state.reasoning_last_progress_token,
        "continue_after_plan": state.continue_after_plan,
        "terminal_disposition": state.terminal_disposition,
    }


def restore_progression(state: Any, data: dict[str, Any]) -> None:
    _restore_semantics(state, data)
    _restore_counters(state, data)
    _restore_terminal(state, data)


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


def _restore_counters(state: Any, data: dict[str, Any]) -> None:
    continuation_attempts = data.get("continuation_attempts", 0)
    if (
        isinstance(continuation_attempts, bool)
        or not isinstance(continuation_attempts, int)
        or continuation_attempts < 0
    ):
        raise ValueError("Checkpoint continuation counter is invalid.")
    reasoning_turns_used = data.get("reasoning_turns_used", 0)
    if (
        isinstance(reasoning_turns_used, bool)
        or not isinstance(reasoning_turns_used, int)
        or reasoning_turns_used < 0
    ):
        raise ValueError("Checkpoint reasoning counter is invalid.")
    state.continuation_attempts = continuation_attempts
    state.reasoning_turns_used = reasoning_turns_used
    raw_cursor = data.get("reasoning_last_history_count")
    if raw_cursor is None:
        # Checkpoints written before the scoped cursor existed must resume at
        # the current history boundary, never treat old history as new work.
        raw_cursor = len(data.get("tool_history") or [])
    if (
        isinstance(raw_cursor, bool)
        or not isinstance(raw_cursor, int)
        or raw_cursor < -1
    ):
        raise ValueError("Checkpoint reasoning history cursor is invalid.")
    state.reasoning_last_history_count = raw_cursor
    token = data.get("reasoning_last_progress_token")
    if token is not None and not isinstance(token, str):
        raise ValueError("Checkpoint reasoning progress token is invalid.")
    state.reasoning_last_progress_token = token
    continue_after_plan = data.get("continue_after_plan", False)
    if not isinstance(continue_after_plan, bool):
        raise ValueError("Checkpoint continuation flag is invalid.")
    state.continue_after_plan = continue_after_plan


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
