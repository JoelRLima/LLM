"""Checkpoint serialization for task-level progression fields."""

from __future__ import annotations

from typing import Any

from agent.reporting.operational_outcome import PUBLIC_TERMINAL_STATUSES

_VALID_TERMINAL_DISPOSITIONS = frozenset({"complete", "block", "fail"}) | (
    PUBLIC_TERMINAL_STATUSES - {"succeeded"}
)


def progression_checkpoint(state: Any) -> dict[str, Any]:
    return {
        "requested_effects": state.requested_effects,
        "executed_effects": state.executed_effects,
        "waived_effects": state.waived_effects,
        "continuation_attempts": state.continuation_attempts,
        "reasoning_turns_used": state.reasoning_turns_used,
        "reasoning_last_history_count": state.reasoning_last_history_count,
        "reasoning_last_progress_token": state.reasoning_last_progress_token,
        "continue_after_plan": state.continue_after_plan,
        "terminal_disposition": state.terminal_disposition,
    }


def restore_progression(state: Any, data: dict[str, Any]) -> None:
    state.requested_effects = [str(item) for item in (data.get("requested_effects") or [])]
    state.executed_effects = [str(item) for item in (data.get("executed_effects") or [])]
    state.waived_effects = [str(item) for item in (data.get("waived_effects") or [])]
    state.continuation_attempts = int(data.get("continuation_attempts") or 0)
    state.reasoning_turns_used = int(data.get("reasoning_turns_used") or 0)
    raw_cursor = data.get("reasoning_last_history_count")
    if raw_cursor is None:
        # Checkpoints written before the scoped cursor existed must resume at
        # the current history boundary, never treat old history as new work.
        raw_cursor = len(data.get("tool_history") or [])
    state.reasoning_last_history_count = int(raw_cursor)
    token = data.get("reasoning_last_progress_token")
    state.reasoning_last_progress_token = str(token) if token is not None else None
    state.continue_after_plan = bool(data.get("continue_after_plan", False))
    terminal = data.get("terminal_disposition")
    if terminal is None:
        state.terminal_disposition = None
        return
    normalized_terminal = str(terminal)
    if normalized_terminal not in _VALID_TERMINAL_DISPOSITIONS:
        raise ValueError("Checkpoint contains an unsupported terminal disposition.")
    state.terminal_disposition = normalized_terminal
