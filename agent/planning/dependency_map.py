"""Causal dependency edges for one normalized plan instance."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Dict, List

from agent.planning.plan_model import Plan, PlanReferenceError, ToolPlanStep, bind_plan_references
from agent.planning.result_bindings import result_is_bindable
from agent.state_progression import current_result_for_step


def _add_dependency(edges: Dict[int, List[int]], consumer: int, producer: int) -> None:
    if producer >= consumer:
        return
    bucket = edges.setdefault(consumer, [])
    if producer not in bucket:
        bucket.append(producer)


def build_dependency_map(
    plan: Plan | Sequence[Mapping[str, Any]],
) -> tuple[Dict[int, List[int]], Dict[tuple[int, int], str]]:
    """Derive stable causal edges from explicit result bindings."""

    if isinstance(plan, Plan):
        return _build_typed_dependency_map(plan)
    # Historical list-shaped callers are decoded once at this explicit
    # boundary.  Dependency semantics thereafter use only the typed owner.
    try:
        typed_plan = bind_plan_references(Plan.from_raw(plan))
    except (PlanReferenceError, ValueError, TypeError):
        return {}, {}
    return _build_typed_dependency_map(typed_plan)


def dependency_succeeded(
    history: Sequence[Mapping[str, Any]],
    producer_id: str,
    file_path: str | None = None,
    *,
    plan_id: str | None = None,
) -> bool:
    """Check the current producer result through its explicit step identity.

    ``file_path`` remains an ignored compatibility parameter for callers that
    used the removed implicit file-production edge.
    """

    current = current_result_for_step(history, producer_id, plan_id=plan_id)
    if current is not None:
        _, item = current
        result = item.get("result")
        return isinstance(result, Mapping) and result_is_bindable(result)
    del file_path
    return False


def dependent_indices(
    plan: Plan | Sequence[Mapping[str, Any]], producer: int
) -> set[int]:
    """Return the transitive consumers of one plan slot."""

    edges, _ = build_dependency_map(plan)
    dependents: set[int] = set()
    frontier = {producer}
    while frontier:
        next_frontier = {
            consumer
            for consumer, producers in edges.items()
            if consumer not in dependents
            and any(item in frontier for item in producers)
        }
        dependents.update(next_frontier)
        frontier = next_frontier
    return dependents


def _build_typed_dependency_map(
    plan: Plan,
) -> tuple[Dict[int, List[int]], Dict[tuple[int, int], str]]:
    edges: Dict[int, List[int]] = {}
    dependency_files: Dict[tuple[int, int], str] = {}
    step_indexes = {step.step_id: index for index, step in enumerate(plan.steps)}
    for index, step in enumerate(plan.steps):
        if not isinstance(step, ToolPlanStep) or step.bindings is None:
            continue
        for binding in step.bindings.values():
            if binding.from_step.step_id is None:
                continue
            producer = step_indexes.get(binding.from_step.step_id)
            if producer is not None:
                _add_dependency(edges, index, producer)
    return edges, dependency_files


__all__ = ["build_dependency_map", "dependent_indices", "dependency_succeeded"]
