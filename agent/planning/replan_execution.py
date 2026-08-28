"""Failure-to-replan orchestration for the plan executor."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from agent.planning.replan import replan
from agent.planning.replan_models import ReplanContext
from agent.runtime.failures import FailureFact, failure_fact_from_legacy_message
from agent.tools.contracts import ToolResult


def attempt_replan(
    orchestrator: Any,
    step: Dict[str, Any],
    objective: str,
    *,
    last_result: Optional[ToolResult] = None,
    last_error: Optional[str] = None,
    failure: FailureFact | None = None,
) -> Optional[List[Dict[str, Any]]]:
    state = orchestrator.agent_state
    selected_result = last_result if last_result is not None else state.last_result
    typed_failure = failure
    if typed_failure is None and selected_result is not None:
        typed_failure = FailureFact.from_tool_result(
            selected_result,
            tool_name=str(step.get("tool", "")),
            step_id=str(step.get("_step_id")) if step.get("_step_id") else None,
        )
    if typed_failure is None:
        typed_failure = failure_fact_from_legacy_message(last_error)
    error = str(last_error or typed_failure.message or typed_failure.code)
    context = ReplanContext(
        task=objective,
        current_step=step,
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
