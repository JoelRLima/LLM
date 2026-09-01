"""Failure-to-replan orchestration for the plan executor."""

from __future__ import annotations

from typing import Any, Optional

from agent.planning.plan_model import Plan, ToolPlanStep
from agent.planning.replan import replan
from agent.planning.replan_models import ReplanContext
from agent.runtime.failures import FailureFact
from agent.tools.contracts import ToolResult


def attempt_replan(
    orchestrator: Any,
    step: ToolPlanStep,
    objective: str,
    *,
    last_result: Optional[ToolResult] = None,
    last_error: Optional[str] = None,
    failure: FailureFact | None = None,
) -> Optional[Plan]:
    state = orchestrator.agent_state
    selected_result = last_result if last_result is not None else state.last_result
    typed_failure = failure
    if typed_failure is None and selected_result is not None:
        typed_failure = FailureFact.from_tool_result(
            selected_result,
            tool_name=step.tool,
            step_id=step.step_id,
        )
    if typed_failure is None:
        typed_failure = FailureFact.unknown(message=last_error)
    error = str(last_error or typed_failure.message or typed_failure.code)
    context = ReplanContext(
        task=objective,
        # ReplanContext is the prompt/model compatibility boundary; it never
        # becomes a second live plan owner.
        current_step=step.to_dict(),
        tool_history=state.tool_history,
        failure=typed_failure,
        last_tool_result=selected_result,
        last_exception=error,
    )
    active_view = getattr(
        getattr(orchestrator, "execution_gateway", None),
        "_active_planning_view",
        None,
    )
    replan_kwargs: dict[str, Any] = {}
    if active_view is not None:
        replan_kwargs["planning_context"] = getattr(
            orchestrator, "planning_context", None
        )
        replan_kwargs["planning_view"] = active_view
    action = replan(context, typed_failure, orchestrator, **replan_kwargs)
    selected_view = getattr(action, "planning_view", None) if action is not None else None
    if selected_view is not None:
        orchestrator.execution_gateway._active_planning_view = selected_view
    return action.steps if action else None


__all__ = ["attempt_replan"]
