"""Lifecycle projections delegated by the orchestration facade."""

from __future__ import annotations

from typing import Any

from agent.error_handler import ErrorHandler
from agent.runtime.failures import FailureFact
from agent.runtime.operational_outcome import project_operational_outcome


def event_plan_step_ids(data: dict[str, Any], state: Any) -> tuple[str | None, str | None]:
    """Promote event-specific plan/step identity over mutable cursor state."""

    plan_id = data.get("plan_id")
    step_id = data.get("step_id")
    return (
        plan_id if isinstance(plan_id, str) else getattr(state, "plan_identity", None),
        step_id if isinstance(step_id, str) else getattr(state, "current_step_id", None),
    )


def is_task_solved(owner: Any) -> bool:
    from agent.planning.task_completion import review_task_completion

    review = review_task_completion(owner)
    outcome = project_operational_outcome(
        owner.agent_state,
        task_failed=bool(getattr(owner, "_task_failed", False)),
        cancelled=bool(getattr(owner, "_cancelled", False)),
    )
    return (
        review.accepted
        and getattr(owner.agent_state, "terminal_disposition", None) in {"complete", "succeeded"}
        and outcome.terminal_status == "succeeded"
        and not outcome.pending_effects
    )


def handle_step_failure(
    owner: Any,
    step_index: int,
    reason: str,
    tool: str = "",
    args: dict[str, Any] | None = None,
    *,
    failure: FailureFact | None = None,
) -> str:
    return str(ErrorHandler.handle_step_failure(
        step_index,
        reason,
        tool,
        args,
        emit_callback=owner._emit,
        verbose=owner.verbose,
        failure=failure,
    ))


__all__ = ["event_plan_step_ids", "handle_step_failure", "is_task_solved"]
