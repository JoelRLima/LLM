"""Small helpers for carrying exact canonical planning views across seams."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from agent.planning.planning_context import PlanningContextError
from agent.planning.presentation import (
    PlanningPresentationSnapshot,
    validate_planning_view_binding,
)


def resume_planning_view(
    orchestrator: Any,
    plan: Sequence[Mapping[str, Any]],
    planner_kind: str = "linear",
) -> PlanningPresentationSnapshot | None:
    """Reconstruct a persisted plan's narrow view without widening eligibility."""

    context = getattr(orchestrator, "planning_context", None)
    current_view = getattr(orchestrator, "get_planning_view", lambda _kind: None)(planner_kind)
    if context is None or current_view is None:
        return None
    names: set[str] = set()
    for item in plan:
        _collect_plan_tool_names(item, names)
    if not names.issubset(current_view.presented_names):
        names.clear()
    return cast(PlanningPresentationSnapshot, context.present(planner_kind, names))


def _collect_plan_tool_names(item: Any, names: set[str]) -> None:
    if not isinstance(item, Mapping):
        return
    if item.get("kind") == "deferred_condition":
        _collect_plan_tool_names(item.get("on_true"), names)
        return
    tool = item.get("tool")
    if isinstance(tool, str) and tool:
        names.add(tool)


def extend_planning_view(
    context: Any,
    planning_view: PlanningPresentationSnapshot | None,
    prefix: Sequence[Mapping[str, Any]],
) -> PlanningPresentationSnapshot | None:
    """Add persisted prefix tools to a validated selected view when needed."""

    if planning_view is None:
        return None
    if context is None:
        raise PlanningContextError("planning view sem contexto canônico")
    validate_planning_view_binding(context, planning_view, "linear")
    prefix_names = {
        str(step.get("tool"))
        for step in prefix
        if isinstance(step, Mapping) and step.get("tool")
    }
    if prefix_names.issubset(planning_view.presented_names):
        return planning_view
    return cast(
        PlanningPresentationSnapshot,
        context.present("linear", planning_view.presented_names | prefix_names),
    )


def selected_view_kwargs(decision: Any) -> dict[str, Any]:
    """Carry a model-selected view through an execution-gateway call."""

    view = getattr(decision, "planning_view", None)
    return {"planning_view": view} if view is not None else {}
