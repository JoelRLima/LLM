"""Small execution hook for the single post-plan reasoning boundary."""

from __future__ import annotations

from typing import Any

from agent.planning.task_completion import continue_after_reasoning_boundary


def handle_boundary(executor: Any, objective: str, enabled: bool = True) -> tuple[bool, str | None, bool]:
    """Run the canonical post-plan boundary.

    ``enabled`` remains in the signature for checkpoint and caller
    compatibility, but it is intentionally not a completion bypass.  A
    model-owned continuation flag may describe the initial plan; it cannot
    suppress the completion decision after a tool plan is exhausted.
    """
    del enabled
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
