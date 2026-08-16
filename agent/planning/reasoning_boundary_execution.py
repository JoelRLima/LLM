"""Small execution hook for the single post-plan reasoning boundary."""

from __future__ import annotations

from typing import Any

from agent.planning.task_completion import continue_after_reasoning_boundary


def handle_boundary(executor: Any, objective: str, enabled: bool) -> tuple[bool, str | None, bool]:
    """Return ``(handled, answer, extended)`` without creating a loop budget."""
    if not enabled:
        return False, None, False
    boundary = continue_after_reasoning_boundary(executor.orchestrator, objective)
    state = executor.orchestrator.agent_state
    if boundary.answer:
        state.continue_after_plan = False
        return True, boundary.answer, False
    if boundary.extended:
        state.continue_after_plan = True
        executor._rebuild_dependency_map()
        return True, None, True
    if boundary.completed:
        state.continue_after_plan = False
        return True, None, False
    return True, None, False
