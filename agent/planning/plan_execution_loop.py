"""Bounded loop body for PlanExecutor."""

from __future__ import annotations

from typing import Any, Dict, Optional, cast

from agent.contracts import ToolResult
from agent.planning.completion_observations import terminal_failure
from agent.planning.reasoning_boundary_execution import handle_boundary
from agent.planning.task_completion import continue_after_observation, mark_unfinished_effect, needs_effect_continuation


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
        handled, answer, extended = handle_boundary(executor, objective, continue_after_plan)
        if answer:
            return None, answer, True, continue_after_plan
        if handled and extended:
            return state.next_pending_index(), None, False, True
        if handled:
            return None, None, True, continue_after_plan
    return index, None, False, continue_after_plan


def run_plan_loop(executor: Any, objective: str, usage: Dict[str, int], continue_after_plan: bool) -> Optional[str]:
    state = executor.orchestrator.agent_state
    last_result: Optional[ToolResult] = None
    index = state.next_pending_index()
    while index is not None:
        iteration = executor._execute_index(index, objective, usage)
        last_result = iteration.result or last_result
        if iteration.stop:
            return cast(Optional[str], iteration.answer)
        index, answer, stop, continue_after_plan = _advance(executor, objective, iteration, continue_after_plan)
        if stop:
            return answer
    if state.pending_effects() and not terminal_failure(executor.orchestrator):
        return mark_unfinished_effect(executor.orchestrator, objective)
    if last_result is not None and not last_result.get("ok"):
        return f"A tarefa não pôde ser concluída. Último erro: {last_result.get('error', 'Erro desconhecido')}"
    return None
