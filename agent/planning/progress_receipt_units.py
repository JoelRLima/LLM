"""Canonical plan/graph unit projection for progress receipts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        converted = to_dict()
        if isinstance(converted, Mapping):
            return converted
    return {}


def _stable_text(value: Any) -> str:
    return str(getattr(value, "value", value)).strip().replace("\\", "/")


def _status(value: Any) -> str:
    return _stable_text(getattr(value, "value", value)).casefold()


def _step_id(step: Any, fallback: Any) -> str:
    mapping = _mapping(step)
    return _stable_text(
        mapping.get("_step_id", mapping.get("step_id", getattr(step, "step_id", fallback)))
    )


def _step_status(record: Any) -> str:
    mapping = _mapping(record)
    return _status(mapping.get("status", getattr(record, "status", "pending")))


def extract_plan_units(
    state: Any,
    plan: Any,
    step_records: Mapping[str, Any] | None,
    graph_state: Any,
) -> tuple[
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    list[dict[str, Any]],
]:
    records = step_records if isinstance(step_records, Mapping) else getattr(state, "step_records", {})
    if not isinstance(records, Mapping):
        records = {}
    raw_units: list[tuple[str, str, str]] = []
    selected_graph = graph_state if graph_state is not None else getattr(state, "task_graph_state", None)
    graph_states = getattr(selected_graph, "states", None)
    if isinstance(graph_states, Mapping):
        for raw_id, raw_state in sorted(graph_states.items(), key=lambda item: str(item[0])):
            raw_units.append((_stable_text(raw_id), "graph", _status(raw_state)))
    elif isinstance(selected_graph, Mapping) and isinstance(selected_graph.get("states"), Mapping):
        for raw_id, raw_state in sorted(selected_graph["states"].items(), key=lambda item: str(item[0])):
            raw_units.append((_stable_text(raw_id), "graph", _status(raw_state)))
    else:
        selected_plan = plan if plan is not None else getattr(state, "plan", ())
        try:
            iterable = tuple(selected_plan or ())
        except TypeError:
            iterable = ()
        for index, step in enumerate(iterable):
            identifier = _step_id(step, index)
            record = records.get(identifier)
            raw_units.append((identifier, "plan", _step_status(record) if record is not None else "pending"))
    pending = tuple(identifier for identifier, _kind, status in raw_units if status == "pending")
    running = tuple(identifier for identifier, _kind, status in raw_units if status == "running")
    completed = tuple(
        identifier
        for identifier, _kind, status in raw_units
        if status in {"completed", "succeeded"}
    )
    unit_projection = [
        {"id": identifier, "kind": kind, "status": status}
        for identifier, kind, status in raw_units
    ]
    return pending, running, completed, tuple(item[0] for item in raw_units), unit_projection


__all__ = ["extract_plan_units"]
