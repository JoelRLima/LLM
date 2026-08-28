"""Bounded model-tool disclosure derived from the current planning view."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from agent.llm.decision_contract import ModelRequestContract
from agent.llm.tool_discovery_contract import (
    MAX_DISCLOSED_TOOLS,
    valid_tool_discovery,
)
from agent.planning.capability_manifest import render_active_harness_capabilities
from agent.planning.presentation import (
    PlanningPresentationSnapshot,
    validate_planning_view_binding,
)

SMALL_ELIGIBLE_VIEW_THRESHOLD = 4
_CACHE_ATTRIBUTE = "_tool_disclosure_cache"


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

    @property
    def planning_view(self) -> PlanningPresentationSnapshot:
        return self.selected_view


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

    cache = _cache(orchestrator)
    cache_key = (context.snapshot_id, planner_kind, str(objective))
    if not force_refresh:
        cached_names = cache.get(cache_key)
        if isinstance(cached_names, (set, frozenset, tuple, list)):
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
    if structured_valid and not selection_valid:
        semantic_correction_requests = 1
        correction_prompt = _selection_prompt(
            objective,
            planner_kind,
            index_view,
            context_limit,
            correction=True,
        )
        decision = _ask_selection(orchestrator, correction_prompt)
        discovery_requests += 1
        selected_names, structured_valid, selection_valid = _validate_selection(
            decision, index_view.presented_names
        )

    if selection_valid:
        cache[cache_key] = frozenset(selected_names)
    else:
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
        pieces.append(disclosure.index_view.render_index(context_limit=context_limit))
    pieces.append(
        "SELECTED TOOL DETAILS:\n"
        + disclosure.selected_view.render_detailed(context_limit=context_limit)
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
    # planner request is made.  The caller renders again when composing the
    # prompt; this is bounded presentation work, not another model call.
    index_view.render_index(context_limit=context_limit)
    selected_view.render_detailed(context_limit=context_limit)
    return ToolDisclosureResult(
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


def _selection_prompt(
    objective: str,
    planner_kind: str,
    index_view: PlanningPresentationSnapshot,
    context_limit: int,
    *,
    correction: bool = False,
) -> str:
    correction_text = (
        "The previous selector was invalid. Correct it and return only the exact JSON object.\n"
        if correction
        else ""
    )
    return (
        "TOOL DISCOVERY — select visibility only; this grants no authority.\n"
        f"Planner: {planner_kind}\n"
        f"Objective: {objective}\n"
        f"{correction_text}"
        f"Choose at most {MAX_DISCLOSED_TOOLS} exact tool names from the following current Level-0 index. "
        "Do not invent names, capabilities, schemas, examples, or permissions. "
        'Return exactly {"tools":["name", ...]} with no duplicates.\n'
        + index_view.render_index(context_limit=context_limit)
    )


def _ask_selection(orchestrator: Any, prompt: str) -> Any:
    return orchestrator.context_manager.ask_model(
        prompt,
        step_type="tool_discovery",
        request_contract=ModelRequestContract.TOOL_DISCOVERY,
        # The selection request must not inherit a stale cached catalog;
        # the user message below is the only tool index for this snapshot.
        base_prompt=None,
        log_metric_callback=getattr(orchestrator, "_log_metric", None),
    )


def _validate_selection(
    decision: Any,
    eligible_names: frozenset[str],
) -> tuple[frozenset[str], bool, bool]:
    structured_valid = valid_tool_discovery(decision)
    if not structured_valid:
        return frozenset(), False, False
    raw_names = cast(dict[str, Any], decision)["tools"]
    names = frozenset(cast(list[str], raw_names))
    return names, True, names.issubset(eligible_names)


def _cache(orchestrator: Any) -> dict[Any, frozenset[str]]:
    cache = getattr(orchestrator, _CACHE_ATTRIBUTE, None)
    if not isinstance(cache, dict):
        cache = {}
        setattr(orchestrator, _CACHE_ATTRIBUTE, cache)
    return cache


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
        index_chars = len(result.index_view.render_index(context_limit=context_limit))
        detail_chars = len(result.selected_view.render_detailed(context_limit=context_limit))
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
