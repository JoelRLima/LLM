"""Bounded selection reuse under an exact task and planning presentation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from llm_agent.agent.planning.planning_context import PlanningContextSnapshot
from llm_agent.agent.planning.presentation import PlanningPresentationSnapshot
from llm_agent.agent.runtime.correlation import RunCorrelation

MAX_CACHED_SELECTIONS = 8
_CACHE_ATTRIBUTE = "_tool_disclosure_cache"


def discovery_scope(owner: Any, objective: str) -> tuple[Any, ...] | None:
    correlation = getattr(owner, "run_correlation", None)
    capabilities = getattr(owner, "allowed_capabilities", None)
    if not isinstance(correlation, RunCorrelation) or not isinstance(capabilities, (set, frozenset)):
        return None
    return (
        correlation, objective, frozenset(capabilities),
        getattr(owner, "task_authority", None), getattr(owner, "application_authority", None),
        getattr(owner, "planning_context", None),
    )


@dataclass(frozen=True, slots=True)
class _Selection:
    context: PlanningContextSnapshot
    view: PlanningPresentationSnapshot
    scope: tuple[Any, ...]
    context_limit: int
    names: frozenset[str]


def _cache(owner: Any) -> dict[Any, Any]:
    cache = getattr(owner, _CACHE_ATTRIBUTE, None)
    if not isinstance(cache, dict):
        cache = {}
        setattr(owner, _CACHE_ATTRIBUTE, cache)
    while len(cache) > MAX_CACHED_SELECTIONS:
        cache.pop(next(iter(cache)))
    return cache


def cached_selection(
    owner: Any, context: PlanningContextSnapshot, view: PlanningPresentationSnapshot,
    objective: str, context_limit: int, *, force_refresh: bool,
) -> frozenset[str] | None:
    cache = _cache(owner)
    key = (context.snapshot_id, view.planner_kind, objective)
    entry = cache.pop(key, None)
    scope = discovery_scope(owner, objective)
    if force_refresh or scope is None or not isinstance(entry, _Selection):
        return None
    if (entry.scope != scope or entry.context != context or entry.view != view
            or entry.context_limit != context_limit):
        return None
    cache[key] = entry
    return entry.names


def remember_selection(
    owner: Any, context: PlanningContextSnapshot, view: PlanningPresentationSnapshot,
    objective: str, context_limit: int, names: frozenset[str],
) -> None:
    scope = discovery_scope(owner, objective)
    if scope is None:
        return
    cache = _cache(owner)
    key = (context.snapshot_id, view.planner_kind, objective)
    cache.pop(key, None)
    cache[key] = _Selection(context, view, scope, context_limit, names)
    while len(cache) > MAX_CACHED_SELECTIONS:
        cache.pop(next(iter(cache)))
