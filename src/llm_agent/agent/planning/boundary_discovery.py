"""Reuse same-task tool visibility for a fresh reasoning-boundary decision."""
from __future__ import annotations

from typing import Any

from llm_agent.agent.llm.admitted_decisions import (
    ReasoningBoundaryExecuteDecision,
    ask_model_decision_with_compatibility,
)
from llm_agent.agent.llm.decision_contract import ModelRequestContract
from llm_agent.agent.planning.disclosure_cache import discovery_scope
from llm_agent.agent.planning.plan_progress import build_plan_progress
from llm_agent.agent.planning.planning_context import PlanningContextError
from llm_agent.agent.planning.planning_view_support import _collect_plan_tool_names
from llm_agent.agent.planning.presentation import PlanningPresentationSnapshot, validate_planning_view_binding
from llm_agent.agent.planning.tool_disclosure import (
    SMALL_ELIGIBLE_VIEW_THRESHOLD,
    ToolDisclosureResult,
    _context_limit,
    _emit,
    _result,
    render_tool_guidance,
)


def _reusable_disclosure(builder: Any, objective: str) -> ToolDisclosureResult | None:
    owner = builder.orchestrator
    prior = getattr(builder, "_last_tool_disclosure", None)
    scope = discovery_scope(owner, objective)
    if scope is None or scope != getattr(builder, "_last_discovery_scope", None):
        return None
    if not isinstance(prior, ToolDisclosureResult) or not prior.selection_valid or not prior.selected_names:
        return None
    if len(prior.index_view.presented_names) <= SMALL_ELIGIBLE_VIEW_THRESHOLD:
        return None
    if prior._rendered_context_limit != _context_limit(owner):
        return None
    context = getattr(owner, "planning_context", None)
    current = getattr(owner, "get_planning_view", lambda kind: None)("linear")
    if context is None or not isinstance(current, PlanningPresentationSnapshot):
        return None
    validate_planning_view_binding(context, current, "linear")
    if current.presented_names != prior.index_view.presented_names:
        return None
    try:
        validate_planning_view_binding(context, prior.index_view, "linear")
        validate_planning_view_binding(context, prior.selected_view, "linear")
    except PlanningContextError:
        return None
    # Revalidate presentation against the current budget, and report zero
    # discovery calls. Observations/progress are never cached by this owner.
    result = _result(
        current, prior.selected_view, discovery_skipped=True, cache_hit=True,
        context_limit=_context_limit(owner),
    )
    _emit(owner, result, cached=True)
    return result


def _ask(builder: Any, objective: str, observations: str, tools: str | None = None) -> Any:
    owner = builder.orchestrator
    progress = build_plan_progress(owner.agent_state)
    prompt = (
        builder._build_reasoning_boundary_prompt(objective, observations, progress)
        if tools is None else
        builder._build_reasoning_boundary_prompt(objective, observations, progress, tool_guidance=tools)
    )
    return ask_model_decision_with_compatibility(
        owner.context_manager, prompt, step_type="continuation_plan",
        base_prompt=getattr(owner, "_cached_base_prompt", None),
        log_metric_callback=owner._log_metric,
        request_contract=ModelRequestContract.REASONING_BOUNDARY_CONTINUATION,
    )


def request_reasoning_boundary(builder: Any, objective: str, observations: str) -> Any:
    disclosure = _reusable_disclosure(builder, objective)
    if disclosure is None:
        return _ask(builder, objective, observations)
    builder._last_planning_view = disclosure.selected_view
    decision = _ask(builder, objective, observations, render_tool_guidance(builder.orchestrator, disclosure))
    if isinstance(decision, ReasoningBoundaryExecuteDecision):
        names: set[str] = set()
        for item in decision.plan:
            _collect_plan_tool_names(item, names)
        if not names.issubset(disclosure.selected_names):
            # No tool or obligation has been applied. Re-discover using the
            # normal exact snapshot, then ask once more with fresh details.
            return _ask(builder, objective, observations)
    return decision


__all__ = ["discovery_scope", "request_reasoning_boundary"]
