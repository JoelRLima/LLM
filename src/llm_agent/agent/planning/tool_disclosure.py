"""Bounded model-tool disclosure derived from the current planning view."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

from llm_agent.agent.llm.tool_discovery_contract import (
    MAX_DISCLOSED_TOOLS,
    valid_tool_discovery,
)
from llm_agent.agent.planning.capability_manifest import render_active_harness_capabilities
from llm_agent.agent.planning.disclosure_cache import cached_selection, remember_selection
from llm_agent.agent.planning.presentation import (
    PlanningPresentationSnapshot,
    validate_planning_view_binding,
)
from llm_agent.agent.planning.tool_disclosure_selection import (
    _ask_selection,
    _authorize_semantic_selection_repair,
    _selection_prompt,
    _validate_selection,
)

SMALL_ELIGIBLE_VIEW_THRESHOLD = 4
@dataclass(frozen=True, slots=True)
class ToolDisclosureResult:
    """One immutable disclosure decision for one exact planning snapshot."""
    index_view: PlanningPresentationSnapshot
    selected_view: PlanningPresentationSnapshot
    selected_names: frozenset[str]
    discovery_requests: int = 0
    semantic_correction_requests: int = 0
    discovery_skipped: bool = False
    cache_hit: bool = False
    selection_valid: bool = True
    structured_decision_valid: bool | None = None
    _rendered_context_limit: int | None = field(default=None, init=False, repr=False, compare=False)
    _rendered_index: str | None = field(default=None, init=False, repr=False, compare=False)
    _rendered_details: str | None = field(default=None, init=False, repr=False, compare=False)

    @property
    def planning_view(self) -> PlanningPresentationSnapshot:
        return self.selected_view

    def render_index(self, *, context_limit: int) -> str:
        if self._rendered_context_limit == context_limit and self._rendered_index is not None:
            return self._rendered_index
        return cast(str, self.index_view.render_index(context_limit=context_limit))

    def render_details(self, *, context_limit: int) -> str:
        if self._rendered_context_limit == context_limit and self._rendered_details is not None:
            return self._rendered_details
        return cast(str, self.selected_view.render_detailed(context_limit=context_limit))
def disclose_tools(
    orchestrator: Any,
    *,
    planner_kind: str,
    objective: str,
    force_refresh: bool = False,
) -> ToolDisclosureResult | None:
    """Select a bounded subset without widening the exact current view."""

    context = getattr(orchestrator, "planning_context", None)
    current_view = getattr(orchestrator, "get_planning_view", lambda _kind: None)(planner_kind)
    if context is None or current_view is None:
        return None
    if not isinstance(current_view, PlanningPresentationSnapshot):
        raise TypeError("canonical planning view has an invalid type")
    validate_planning_view_binding(context, current_view, planner_kind)

    context_limit = _context_limit(orchestrator)
    index_view = context.present(planner_kind, current_view.presented_names)
    eligible_count = len(index_view.presented_names)
    if eligible_count <= SMALL_ELIGIBLE_VIEW_THRESHOLD:
        result = _result(
            index_view,
            index_view,
            discovery_skipped=True,
            selection_valid=True,
            context_limit=context_limit,
        )
        _emit(orchestrator, result, cached=False)
        return result

    cached_names = cached_selection(
        orchestrator, context, index_view, objective, context_limit, force_refresh=force_refresh,
    )
    if cached_names is not None:
        cached_sequence = list(cached_names)
        candidate = frozenset(cached_names)
        if candidate.issubset(index_view.presented_names) and valid_tool_discovery(
            {"tools": cached_sequence}
        ):
            selected_view = context.present(planner_kind, candidate)
            result = _result(
                index_view,
                selected_view,
                cache_hit=True,
                selection_valid=True,
                context_limit=context_limit,
            )
            _emit(orchestrator, result, cached=True)
            return result

    prompt = _selection_prompt(objective, planner_kind, index_view, context_limit)
    decision = _ask_selection(orchestrator, prompt)
    discovery_requests = 1
    selected_names, structured_valid, selection_valid = _validate_selection(
        decision, index_view.presented_names
    )
    semantic_correction_requests = 0
    if structured_valid and not selection_valid and _authorize_semantic_selection_repair(orchestrator):
        semantic_correction_requests = 1
        correction_prompt = _selection_prompt(
            objective,
            planner_kind,
            index_view,
            context_limit,
            correction=True,
            invalid_positions=tuple(
                index for index, name in enumerate(decision.tools)
                if name not in index_view.presented_names
            ),
        )
        decision = _ask_selection(orchestrator, correction_prompt)
        discovery_requests += 1
        selected_names, structured_valid, selection_valid = _validate_selection(
            decision, index_view.presented_names
        )

    if not selection_valid:
        # Fail closed: an invalid selector never receives a detailed card and
        # never creates a new eligible tool name.
        selected_names = frozenset()
    selected_view = context.present(planner_kind, selected_names)
    result = _result(
        index_view,
        selected_view,
        discovery_requests=discovery_requests,
        semantic_correction_requests=semantic_correction_requests,
        selection_valid=selection_valid,
        structured_decision_valid=structured_valid,
        context_limit=context_limit,
    )
    if selection_valid:
        remember_selection(orchestrator, context, index_view, objective, context_limit, selected_names)
    _emit(orchestrator, result, cached=False)
    return result

def render_tool_guidance(
    orchestrator: Any,
    disclosure: ToolDisclosureResult,
    *,
    include_index: bool = True,
) -> str:
    """Render the index plus only selected detailed cards and the harness manual."""

    context_limit = _context_limit(orchestrator)
    pieces: list[str] = []
    if include_index and len(disclosure.index_view.tools) > SMALL_ELIGIBLE_VIEW_THRESHOLD:
        pieces.append(disclosure.render_index(context_limit=context_limit))
    pieces.append(
        "SELECTED TOOL DETAILS:\n"
        + disclosure.render_details(context_limit=context_limit)
    )
    pieces.append(
        render_active_harness_capabilities(
            orchestrator,
            planner_kind=disclosure.selected_view.planner_kind,
        )
    )
    return "\n\n".join(pieces)

def render_selected_tool_detail(
    orchestrator: Any,
    *,
    planner_kind: str,
    tool_name: str,
) -> str:
    """Render one already-eligible tool for constrained validation repair."""

    context = getattr(orchestrator, "planning_context", None)
    view = getattr(orchestrator, "get_planning_view", lambda _kind: None)(planner_kind)
    if context is None or view is None or tool_name not in view.presented_names:
        return ""
    validate_planning_view_binding(context, view, planner_kind)
    selected = context.present(planner_kind, {tool_name})
    return cast(str, selected.render_detailed(context_limit=_context_limit(orchestrator)))

def _result(
    index_view: PlanningPresentationSnapshot,
    selected_view: PlanningPresentationSnapshot,
    *,
    discovery_requests: int = 0,
    semantic_correction_requests: int = 0,
    discovery_skipped: bool = False,
    cache_hit: bool = False,
    selection_valid: bool = True,
    structured_decision_valid: bool | None = None,
    context_limit: int,
) -> ToolDisclosureResult:
    # Render now so an oversized selected detailed view fails closed before a
    # planner request is made. Reuse these exact validated strings only for
    # this immutable result and the same context limit.
    index_text = index_view.render_index(context_limit=context_limit)
    detail_text = selected_view.render_detailed(context_limit=context_limit)
    result = ToolDisclosureResult(
        index_view=index_view,
        selected_view=selected_view,
        selected_names=selected_view.presented_names,
        discovery_requests=discovery_requests,
        semantic_correction_requests=semantic_correction_requests,
        discovery_skipped=discovery_skipped,
        cache_hit=cache_hit,
        selection_valid=selection_valid,
        structured_decision_valid=structured_decision_valid,
    )
    object.__setattr__(result, "_rendered_context_limit", context_limit)
    object.__setattr__(result, "_rendered_index", index_text)
    object.__setattr__(result, "_rendered_details", detail_text)
    return result






def _context_limit(orchestrator: Any) -> int:
    return int(
        getattr(getattr(getattr(orchestrator, "session", None), "hardware_profile", None), "context_limit", 8_192)
    )


def _emit(orchestrator: Any, result: ToolDisclosureResult, *, cached: bool) -> None:
    emit = getattr(orchestrator, "_emit", None)
    if not callable(emit):
        return
    context_limit = _context_limit(orchestrator)
    try:
        index_chars = len(result.render_index(context_limit=context_limit))
        detail_chars = len(result.render_details(context_limit=context_limit))
    except Exception:
        index_chars = detail_chars = 0
    emit(
        "tool_discovery",
        {
            "eligible_tools": len(result.index_view.presented_names),
            "selected_tools": sorted(result.selected_names),
            "index_chars": index_chars,
            "detail_chars": detail_chars,
            "discovery_requests": result.discovery_requests,
            "semantic_correction_requests": result.semantic_correction_requests,
            "discovery_skipped": result.discovery_skipped,
            "cache_hit": cached or result.cache_hit,
            "selection_valid": result.selection_valid,
            "structured_decision_valid": result.structured_decision_valid,
        },
    )


__all__ = ["MAX_DISCLOSED_TOOLS", "SMALL_ELIGIBLE_VIEW_THRESHOLD", "ToolDisclosureResult", "disclose_tools", "render_selected_tool_detail", "render_tool_guidance"]
