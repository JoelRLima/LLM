"""Route adapters for applying a denied task-policy result."""

from __future__ import annotations

from typing import Any

from agent.planning.task_completion import mark_terminal_blocked, mark_terminal_cancelled
from agent.runtime.task_policy import TaskPolicyDecision, TaskPolicyResult
from agent.watchdog import Watchdog


def policy_terminal_answer(
    orchestrator: Any,
    result: TaskPolicyResult,
    *,
    step_index: int | None = None,
) -> str | None:
    """Project one denied policy result through existing task authorities."""

    if result.allowed:
        return None
    state = getattr(orchestrator, "agent_state", None)
    reason = result.message or result.reason_code
    if state is not None and step_index is not None:
        if result.decision is TaskPolicyDecision.CANCELLED:
            mark_cancelled = getattr(state, "mark_step_cancelled", None)
            if callable(mark_cancelled):
                mark_cancelled(step_index, reason)
            else:
                state.mark_step_skipped(step_index, reason)
            _emit_step(orchestrator, "step_cancelled", step_index, reason)
        elif result.decision is TaskPolicyDecision.WATCHDOG_NO_PROGRESS:
            state.mark_step_unverified(step_index, reason)
            _emit_step(orchestrator, "step_unverified", step_index, reason)
        elif result.decision is TaskPolicyDecision.WATCHDOG_REPEATED_ERROR:
            state.mark_step_failed(step_index, reason)
            _emit_step(orchestrator, "step_failed", step_index, reason)
        else:
            state.mark_step_blocked(step_index, reason)
            _emit_step(orchestrator, "step_blocked", step_index, reason)
    if result.decision is TaskPolicyDecision.CANCELLED:
        return mark_terminal_cancelled(orchestrator, reason or "Task cancelled before admission.")
    fail_task = getattr(orchestrator, "fail_task", None)
    if callable(fail_task):
        fail_task()
    return mark_terminal_blocked(
        orchestrator,
        reason_code=result.reason_code,
        message=reason or "Task blocked by the execution policy.",
        status=result.terminal_status or "block",
    )


def _emit_step(orchestrator: Any, event_type: str, index: int, reason: str) -> None:
    emit = getattr(orchestrator, "_emit", None)
    if callable(emit):
        emit(
            event_type,
            {
                "step": index + 1,
                "step_id": orchestrator.agent_state.get_step_id(index),
                "reason": reason,
            },
        )


def start_hierarchical_lifecycle(lifecycle: Any, macro_plan: Any) -> None:
    if isinstance(lifecycle, dict):
        lifecycle.clear()
        lifecycle.update(
            {
                "status": "running",
                "objective": str(macro_plan.objective),
                "macro_step_ids": [str(item.id) for item in macro_plan.steps],
                "completed_macro_step_ids": [],
                "current_macro_step_id": None,
            }
        )


def complete_hierarchical_macro(lifecycle: Any, step_id: Any) -> None:
    if isinstance(lifecycle, dict):
        completed = list(lifecycle.get("completed_macro_step_ids", []))
        completed.append(str(step_id))
        lifecycle["completed_macro_step_ids"] = completed
        lifecycle["current_macro_step_id"] = None


def finish_hierarchical_lifecycle(lifecycle: Any) -> None:
    if isinstance(lifecycle, dict):
        lifecycle["status"] = "completed"
        lifecycle["current_macro_step_id"] = None


def hierarchical_watchdog_reason(orchestrator: Any, agent_state: Any, policy: Any) -> str | None:
    return Watchdog.check_all(
        getattr(orchestrator, "_task_start_time", None),
        getattr(agent_state, "tool_history", []),
        getattr(getattr(orchestrator, "session", None), "config", {}) or {},
        policy.active_elapsed_seconds,
    )


def apply_hierarchical_policy_denial(
    orchestrator: Any,
    tracker: Any,
    summarizer: Any,
    lifecycle: Any,
    step: Any,
    admission: TaskPolicyResult,
) -> bool:
    summary = policy_terminal_answer(orchestrator, admission) or admission.message
    if admission.decision is TaskPolicyDecision.CANCELLED:
        tracker.mark_cancelled(step.id, summary)
    elif admission.decision is TaskPolicyDecision.WATCHDOG_NO_PROGRESS:
        tracker.mark_unverified(step.id, summary)
    else:
        tracker.mark_blocked(step.id, summary)
    summarizer.add(f"## {step.title}\n{summary}")
    if isinstance(lifecycle, dict):
        lifecycle["current_macro_step_id"] = None
    return admission.decision is not TaskPolicyDecision.CANCELLED


__all__ = [
    "apply_hierarchical_policy_denial",
    "complete_hierarchical_macro",
    "finish_hierarchical_lifecycle",
    "hierarchical_watchdog_reason",
    "policy_terminal_answer",
    "start_hierarchical_lifecycle",
]
