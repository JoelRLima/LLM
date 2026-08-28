"""Task-level effect progression helpers for AgentState."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agent.planning.task_semantics import TaskSemantics


def reset_task_progression(
    state: Any,
    requested_effects: Sequence[str] = (),
    *,
    preserve_semantics: bool = False,
) -> None:
    semantics = getattr(state, "task_semantics", None)
    if isinstance(semantics, TaskSemantics):
        if preserve_semantics:
            semantics.reset_progress()
        elif requested_effects:
            state.set_task_semantics(TaskSemantics.from_legacy("", requested_effects))
        else:
            state.set_task_semantics(TaskSemantics.empty())
    else:
        state.requested_effects = list(dict.fromkeys(requested_effects))
        state.executed_effects = []
        state.waived_effects = []
    recovery_budget = getattr(state, "recovery_budget", None)
    if recovery_budget is not None:
        recovery_budget.reset()
    else:
        state.continuation_attempts = 0
    state._task_rollback_occurred = False
    state._task_rollback_succeeded = None
    if recovery_budget is None:
        counts = getattr(state, "replan_counts", None)
        if isinstance(counts, dict):
            counts.clear()
            counts.update({"total": 0, "heuristic": 0, "llm": 0})
        state.reasoning_turns_used = 0
    state.reasoning_last_history_count = 0
    state.reasoning_last_progress_token = None
    state.continue_after_plan = False
    clear_terminal = getattr(state, "clear_terminal_disposition", None)
    if callable(clear_terminal):
        clear_terminal()
    else:
        state.terminal_disposition = None


def record_executed_effect(
    state: Any,
    effect: str,
    *,
    evidence_ref: int | str | None = None,
    allow_legacy: bool = False,
    effect_authority: Any = None,
) -> None:
    semantics = getattr(state, "task_semantics", None)
    if isinstance(semantics, TaskSemantics):
        if effect:
            semantics.record_effect(
                effect,
                evidence_ref=evidence_ref,
                allow_legacy=allow_legacy,
                effect_authority=effect_authority,
            )
        return
    if effect and effect not in state.executed_effects:
        state.executed_effects.append(effect)


def waive_effect(
    state: Any,
    effect: str,
    *,
    evidence_ref: int | str | None = None,
    allow_legacy: bool = False,
    effect_authority: Any = None,
) -> None:
    semantics = getattr(state, "task_semantics", None)
    if isinstance(semantics, TaskSemantics):
        if effect:
            semantics.waive_effect(
                effect,
                evidence_ref=evidence_ref,
                allow_legacy=allow_legacy,
                effect_authority=effect_authority,
            )
        return
    if effect and effect not in state.waived_effects:
        state.waived_effects.append(effect)


def pending_effects(state: Any) -> tuple[str, ...]:
    semantics = getattr(state, "task_semantics", None)
    if isinstance(semantics, TaskSemantics):
        return semantics.pending_effects()
    satisfied = set(state.executed_effects) | set(state.waived_effects)
    return tuple(effect for effect in state.requested_effects if effect not in satisfied)


def current_result_for_step(
    history: Sequence[Mapping[str, Any]],
    step_id: str,
    *,
    plan_id: str | None = None,
) -> tuple[int, Mapping[str, Any]] | None:
    """Return the latest attempt for a logical step (one-based index)."""

    for index in range(len(history) - 1, -1, -1):
        item = history[index]
        if not isinstance(item, Mapping) or item.get("step_id") != step_id:
            continue
        recorded_plan_id = item.get("plan_id")
        if plan_id is not None and recorded_plan_id not in (None, plan_id):
            continue
        if item.get("step_id") == step_id:
            return index + 1, item
    return None
