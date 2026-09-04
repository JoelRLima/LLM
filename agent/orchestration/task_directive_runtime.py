"""Ephemeral runtime effects for an admitted task directive."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.llm.session_requests import _integer
from agent.orchestration.operational_modes import refresh_capability_projection
from agent.runtime.task_directives import TaskRunDirective


@dataclass(frozen=True, slots=True)
class TaskDirectiveRuntimeRestore:
    """Task-entry value needed to restore the session after one run."""

    thinking_budget: int


def apply_task_run_directive_runtime(
    orchestrator: Any,
    directive: TaskRunDirective,
) -> TaskDirectiveRuntimeRestore:
    """Apply one directive's temporary capability and reasoning effects."""

    if not isinstance(directive, TaskRunDirective):
        raise TypeError("directive must be a TaskRunDirective")
    session = getattr(orchestrator, "session", None)
    if session is None:
        raise AttributeError("orchestrator session is required")

    baseline = _integer(getattr(session, "thinking_budget", 0), 0)
    restore = TaskDirectiveRuntimeRestore(thinking_budget=baseline)
    previous_ceiling = getattr(orchestrator, "_task_directive_capability_ceiling", None)
    orchestrator._task_directive_capability_ceiling = directive.capability_ceiling()
    try:
        _refresh_capability_projection_if_available(orchestrator)
        session.thinking_budget = directive.effective_reasoning_budget(baseline)
    except Exception:
        session.thinking_budget = restore.thinking_budget
        orchestrator._task_directive_capability_ceiling = previous_ceiling
        _refresh_capability_projection_if_available(orchestrator)
        raise
    return restore


def restore_task_run_directive_runtime(
    orchestrator: Any,
    restore: TaskDirectiveRuntimeRestore | None,
) -> None:
    """Restore task-entry runtime state and remove the task-local ceiling."""

    if restore is None:
        return
    if not isinstance(restore, TaskDirectiveRuntimeRestore):
        raise TypeError("restore must be a TaskDirectiveRuntimeRestore")
    session = getattr(orchestrator, "session", None)
    try:
        if session is not None:
            session.thinking_budget = restore.thinking_budget
    finally:
        orchestrator._task_directive_capability_ceiling = None
        _refresh_capability_projection_if_available(orchestrator)


def _refresh_capability_projection_if_available(orchestrator: Any) -> None:
    """Keep small compatibility test doubles usable without a full facade."""

    if not all(
        hasattr(orchestrator, name)
        for name in ("_persona_allowed_capabilities", "_operational_mode", "tool_registry")
    ):
        return
    refresh_capability_projection(orchestrator)


__all__ = [
    "TaskDirectiveRuntimeRestore",
    "apply_task_run_directive_runtime",
    "restore_task_run_directive_runtime",
]
