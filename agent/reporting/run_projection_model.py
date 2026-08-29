"""Immutable value object published with a canonical run snapshot."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
from typing import Any


def thaw_projection(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): thaw_projection(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw_projection(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class RunProjectionFacts:
    objective: str | None
    tools: tuple[Mapping[str, Any], ...]
    incidents: tuple[Mapping[str, Any], ...]
    proposed_files: tuple[str, ...]
    validation: Mapping[str, Any]
    rollback: Mapping[str, Any]
    executed: bool | None
    repair_count: int
    replan_count: int
    report_steps: tuple[Mapping[str, Any], ...]
    invocation_evidence: tuple[Mapping[str, Any], ...]
    planner_outcome: str | None
    event_summary: tuple[Mapping[str, Any], ...]
    replan_events: tuple[Mapping[str, Any], ...]
    report_start_time: str
    report_end_time: str
    canonical_plan: Any
    route_events: tuple[Mapping[str, Any], ...]
    validation_events: tuple[Mapping[str, Any], ...]
    output_chars: int
    output_truncated: bool

    def to_dict(self) -> dict[str, Any]:
        return {item.name: thaw_projection(getattr(self, item.name)) for item in fields(self)}


__all__ = ["RunProjectionFacts", "thaw_projection"]
