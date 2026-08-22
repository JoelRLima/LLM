"""Bounded loop body for PlanExecutor."""

from __future__ import annotations

from typing import Any, Dict, Optional, cast

from agent.contracts import ToolResult
from agent.planning.completion_observations import terminal_failure
from agent.planning.reasoning_boundary_execution import handle_boundary
from agent.planning.task_completion import (
    allow_linear_completion,
    continue_after_observation,
    mark_reasoning_boundary_blocked,
    mark_unfinished_effect,
    mark_unfinished_obligation,
    needs_effect_continuation,
)


def _advance(executor: Any, objective: str, iteration: Any, continue_after_plan: bool) -> tuple[int | None, str | None, bool, bool]:
    state = executor.orchestrator.agent_state
    index = state.next_pending_index(iteration.next_index)
    if index is not None:
        return index, None, False, continue_after_plan
    if index is None and needs_effect_continuation(executor.orchestrator, objective):
        answer = continue_after_observation(executor.orchestrator, objective)
        if isinstance(answer, str) and answer:
            return None, answer, True, continue_after_plan
        index = state.next_pending_index()
    if index is None and not terminal_failure(executor.orchestrator):
        # Plan exhaustion is an observation frontier, never completion.  The
        # legacy flag is retained for checkpoint compatibility but has no
        # authority to skip the canonical boundary.
        handled, answer, extended = handle_boundary(executor, objective, continue_after_plan)
        if answer:
            return None, answer, True, continue_after_plan
        if handled and extended:
            next_index = state.next_pending_index()
            if next_index is None:
                return (
                    None,
                    mark_reasoning_boundary_blocked(executor.orchestrator, objective),
                    True,
                    continue_after_plan,
                )
            return next_index, None, False, True
        if handled:
            return None, None, True, continue_after_plan
    return index, None, False, continue_after_plan


def _initial_index(
    executor: Any,
    objective: str,
    continue_after_plan: bool,
) -> tuple[int | None, str | None]:
    state = executor.orchestrator.agent_state
    index = state.next_pending_index()
    eligible = getattr(state, "terminal_disposition", None) in (None, "complete")
    if index is not None or terminal_failure(executor.orchestrator) or not eligible:
        return index, None
    handled, answer, extended = handle_boundary(
        executor, objective, continue_after_plan
    )
    if answer:
        return None, answer
    if handled and not extended:
        return None, None
    index = state.next_pending_index()
    if index is None:
        return None, mark_reasoning_boundary_blocked(executor.orchestrator, objective)
    return index, None


def _finish_loop(
    executor: Any,
    objective: str,
    last_result: Optional[ToolResult],
) -> Optional[str]:
    state = executor.orchestrator.agent_state
    if state.pending_effects() and not terminal_failure(executor.orchestrator):
        return mark_unfinished_effect(executor.orchestrator, objective)
    pending_obligations = tuple(getattr(state, "pending_obligations", lambda: ())())
    if pending_obligations and not terminal_failure(executor.orchestrator):
        return mark_unfinished_obligation(executor.orchestrator, objective)
    blocked_obligations = tuple(getattr(state, "blocked_obligations", lambda: ())())
    if blocked_obligations and not terminal_failure(executor.orchestrator):
        return allow_linear_completion(executor.orchestrator, objective)
    if last_result is not None and not last_result.get("ok"):
        canonical = allow_linear_completion(executor.orchestrator, objective)
        if canonical is not None:
            return canonical
        return (
            "A tarefa não pôde ser concluída. Último erro: "
            f"{last_result.get('error', 'Erro desconhecido')}"
        )
    return None


def run_plan_loop(executor: Any, objective: str, usage: Dict[str, int], continue_after_plan: bool) -> Optional[str]:
    last_result: Optional[ToolResult] = None
    index, initial_answer = _initial_index(executor, objective, continue_after_plan)
    if initial_answer:
        return initial_answer
    while index is not None:
        iteration = executor._execute_index(index, objective, usage)
        last_result = iteration.result or last_result
        if iteration.stop:
            return cast(Optional[str], iteration.answer)
        index, answer, stop, continue_after_plan = _advance(executor, objective, iteration, continue_after_plan)
        if stop:
            return answer
    return _finish_loop(executor, objective, last_result)
