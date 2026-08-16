"""Causal dependency edges for one normalized plan instance."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence

from agent.planning.result_bindings import referenced_step_ids
from agent.state_progression import current_result_for_step


def _add_dependency(edges: Dict[int, List[int]], consumer: int, producer: int) -> None:
    if producer >= consumer:
        return
    bucket = edges.setdefault(consumer, [])
    if producer not in bucket:
        bucket.append(producer)


def build_dependency_map(
    plan: Sequence[Mapping[str, Any]],
) -> tuple[Dict[int, List[int]], Dict[tuple[int, int], str]]:
    """Derive stable binding and legacy file-production edges for one plan."""

    edges: Dict[int, List[int]] = {}
    dependency_files: Dict[tuple[int, int], str] = {}
    step_indexes = {
        str(step.get("_step_id")): index
        for index, step in enumerate(plan)
        if isinstance(step, Mapping) and isinstance(step.get("_step_id"), str)
    }

    for index, step in enumerate(plan):
        if not isinstance(step, Mapping) or not step.get("bindings"):
            continue
        for source_id in referenced_step_ids([step]):
            producer = step_indexes.get(source_id)
            if producer is not None:
                _add_dependency(edges, index, producer)

    producers: Dict[str, int] = {}
    for index, step in enumerate(plan):
        args = step.get("args") if isinstance(step, Mapping) else {}
        args = args if isinstance(args, Mapping) else {}
        file_path = str(args.get("file_path") or args.get("target") or "")
        if step.get("tool") == "file_writer" and file_path:
            producers[file_path] = index
        elif step.get("tool") in ("file_reader", "code_analyzer") and file_path in producers:
            producer = producers[file_path]
            _add_dependency(edges, index, producer)
            dependency_files[(index, producer)] = file_path
    return edges, dependency_files


def dependency_succeeded(
    history: Sequence[Mapping[str, Any]],
    producer_id: str,
    file_path: str | None = None,
) -> bool:
    """Check the current producer result without scanning unrelated steps."""

    current = current_result_for_step(history, producer_id)
    if current is not None:
        _, item = current
        result = item.get("result")
        return isinstance(result, Mapping) and result.get("ok") is True
    if not file_path:
        return False
    by_file = [
        item
        for item in history
        if item.get("tool") == "file_writer"
        and (item.get("args") or {}).get("file_path") == file_path
    ]
    return bool(by_file and by_file[-1].get("result", {}).get("ok"))


def dependent_indices(plan: Sequence[Mapping[str, Any]], producer: int) -> set[int]:
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


__all__ = ["build_dependency_map", "dependent_indices", "dependency_succeeded"]
