"""Task-level effect progression helpers for AgentState."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def reset_task_progression(state: Any, requested_effects: Sequence[str] = ()) -> None:
    state.requested_effects = list(dict.fromkeys(requested_effects))
    state.executed_effects = []
    state.waived_effects = []
    state.continuation_attempts = 0
    state.reasoning_turns_used = 0
    state.reasoning_last_history_count = 0
    state.reasoning_last_progress_token = None
    state.continue_after_plan = False
    state.terminal_disposition = None


def record_executed_effect(state: Any, effect: str) -> None:
    if effect and effect not in state.executed_effects:
        state.executed_effects.append(effect)


def waive_effect(state: Any, effect: str) -> None:
    if effect and effect not in state.waived_effects:
        state.waived_effects.append(effect)


def pending_effects(state: Any) -> tuple[str, ...]:
    satisfied = set(state.executed_effects) | set(state.waived_effects)
    return tuple(effect for effect in state.requested_effects if effect not in satisfied)


def current_result_for_step(
    history: Sequence[Mapping[str, Any]], step_id: str
) -> tuple[int, Mapping[str, Any]] | None:
    """Return the latest attempt for a logical step (one-based index)."""

    for index in range(len(history) - 1, -1, -1):
        item = history[index]
        if item.get("step_id") == step_id:
            return index + 1, item
    return None
