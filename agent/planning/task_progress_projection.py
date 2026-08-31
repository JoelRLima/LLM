"""Pure, read-only projection of execution progress.

The projection reports what execution records say.  It never decides whether
the task succeeded; that remains the responsibility of ``OperationalOutcome``
and the existing semantic/evidence completion owners.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any

from agent.execution_state import StepStatus


class ProgressStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    UNVERIFIED = "unverified"
    PENDING = "pending"
    RUNNING = "running"


_ALL_STATUSES = tuple(ProgressStatus)
_TERMINAL_STATUSES = frozenset(
    {
        ProgressStatus.SUCCEEDED,
        ProgressStatus.FAILED,
        ProgressStatus.SKIPPED,
        ProgressStatus.BLOCKED,
        ProgressStatus.CANCELLED,
        ProgressStatus.UNVERIFIED,
    }
)


@dataclass(frozen=True, slots=True)
class TaskProgressProjection:
    """Immutable progress facts suitable for prompts, trackers, and reports."""

    statuses: tuple[ProgressStatus, ...]
    counts: Mapping[str, int]
    total_units: int
    successful_units: int
    terminal_units: int
    successful_completion_percent: float
    terminal_coverage_percent: float
    operational_status: str | None = None
    semantic_counts: Mapping[str, int] = MappingProxyType({})
    semantic_evidence_complete: bool | None = None
    hierarchy: Mapping[str, Any] = MappingProxyType({})

    def __post_init__(self) -> None:
        object.__setattr__(self, "statuses", tuple(self.statuses))
        object.__setattr__(self, "counts", _freeze_counts(self.counts))
        object.__setattr__(self, "semantic_counts", _freeze_counts(self.semantic_counts))
        object.__setattr__(self, "hierarchy", _freeze_mapping(self.hierarchy))

    @property
    def success_percentage(self) -> float:
        return self.successful_completion_percent

    @property
    def terminal_percentage(self) -> float:
        return self.terminal_coverage_percent

    @property
    def is_operational_success(self) -> bool:
        """Expose only an explicit outcome fact; percentages are not success."""

        return self.operational_status == "succeeded"

    def to_dict(self) -> dict[str, Any]:
        return {
            "statuses": [status.value for status in self.statuses],
            "counts": dict(self.counts),
            "total_units": self.total_units,
            "successful_units": self.successful_units,
            "terminal_units": self.terminal_units,
            "successful_completion_percent": self.successful_completion_percent,
            "terminal_coverage_percent": self.terminal_coverage_percent,
            "operational_status": self.operational_status,
            "semantic_counts": dict(self.semantic_counts),
            "semantic_evidence_complete": self.semantic_evidence_complete,
            "hierarchy": _thaw(self.hierarchy),
        }


def build_task_progress_projection(
    state: Any = None,
    operational_outcome: Any = None,
    *,
    step_records: Mapping[str, Any] | None = None,
    statuses: Sequence[Any] | None = None,
    graph_state: Any = None,
    hierarchy_state: Any = None,
) -> TaskProgressProjection:
    """Build progress facts without mutating any source owner.

    ``graph_state`` is used when supplied because graph nodes are the active
    executable units for that route.  Otherwise the canonical plan records
    are projected in plan order.  Explicit ``statuses`` is a small testing and
    compatibility seam for callers that already own an ordered status view.
    """

    selected_graph = graph_state if graph_state is not None else getattr(state, "task_graph_state", None)
    raw_statuses = (
        list(statuses)
        if statuses is not None
        else _graph_statuses(selected_graph)
        if selected_graph is not None
        else _plan_statuses(state, step_records)
    )
    normalized = tuple(_coerce_status(item) for item in raw_statuses)
    counts = Counter(status.value for status in normalized)
    for status in _ALL_STATUSES:
        counts.setdefault(status.value, 0)
    total = len(normalized)
    successful = counts[ProgressStatus.SUCCEEDED.value]
    terminal = sum(counts[status.value] for status in _TERMINAL_STATUSES)
    semantics = getattr(state, "task_semantics", None)
    semantic_items = _semantic_items(semantics)
    semantic_counts = Counter(str(item.get("status", "pending")) for item in semantic_items)
    evidence_complete = getattr(state, "terminal_evidence_complete", None)
    if callable(evidence_complete):
        evidence_complete = bool(evidence_complete())
    elif not isinstance(evidence_complete, bool):
        evidence_complete = None
    hierarchy = _hierarchy_projection(
        hierarchy_state if hierarchy_state is not None else getattr(state, "hierarchical_lifecycle", None)
    )
    return TaskProgressProjection(
        statuses=normalized,
        counts=counts,
        total_units=total,
        successful_units=successful,
        terminal_units=terminal,
        successful_completion_percent=_percent(successful, total),
        terminal_coverage_percent=_percent(terminal, total),
        operational_status=_outcome_status(
            operational_outcome
            if operational_outcome is not None
            else getattr(state, "operational_outcome", None)
        ),
        semantic_counts=semantic_counts,
        semantic_evidence_complete=evidence_complete,
        hierarchy=hierarchy,
    )


def _plan_statuses(state: Any, step_records: Mapping[str, Any] | None) -> list[Any]:
    records = step_records if step_records is not None else getattr(state, "step_records", {})
    if not isinstance(records, Mapping):
        records = {}
    plan = getattr(state, "plan", None)
    if plan is not None:
        selected: list[Any] = []
        for index, _step in enumerate(plan):
            step_id = _step_id(state, index, _step)
            record = records.get(step_id)
            selected.append(getattr(record, "status", record if record is not None else StepStatus.PENDING))
        if selected:
            return selected
    return [getattr(record, "status", record) for record in records.values()]


def _graph_statuses(graph_state: Any) -> list[Any]:
    states = getattr(graph_state, "states", None)
    if isinstance(states, Mapping):
        return list(states.values())
    if isinstance(graph_state, Mapping):
        raw = graph_state.get("states", graph_state)
        return list(raw.values()) if isinstance(raw, Mapping) else []
    return []


def _step_id(state: Any, index: int, step: Any) -> str:
    getter = getattr(state, "get_step_id", None)
    if callable(getter):
        try:
            return str(getter(index))
        except (IndexError, KeyError, TypeError):
            pass
    return str(getattr(step, "step_id", getattr(step, "id", index)))


def _coerce_status(value: Any) -> ProgressStatus:
    raw = getattr(value, "value", value)
    if raw == StepStatus.COMPLETED.value:
        raw = ProgressStatus.SUCCEEDED.value
    try:
        return ProgressStatus(str(raw))
    except ValueError:
        return ProgressStatus.PENDING


def _semantic_items(semantics: Any) -> list[Mapping[str, Any]]:
    snapshot = getattr(semantics, "snapshot", None)
    if not callable(snapshot):
        return []
    try:
        values = snapshot()
    except Exception:
        return []
    return [item for item in values if isinstance(item, Mapping)] if isinstance(values, Sequence) else []


def _hierarchy_projection(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        return MappingProxyType({})
    raw_statuses = value.get("statuses")
    if isinstance(raw_statuses, Sequence) and not isinstance(raw_statuses, (str, bytes)):
        counts = Counter(str(getattr(item, "value", item)) for item in raw_statuses)
        projected = dict(value)
        projected["counts"] = dict(counts)
        return projected
    return value


def _outcome_status(value: Any) -> str | None:
    if isinstance(value, Mapping):
        raw = value.get("terminal_status", value.get("status"))
    else:
        raw = getattr(value, "terminal_status", None)
    return str(getattr(raw, "value", raw)) if raw is not None else None


def _percent(value: int, total: int) -> float:
    return round(value / total * 100.0, 1) if total else 0.0


def _freeze_counts(value: Mapping[str, int]) -> Mapping[str, int]:
    return MappingProxyType({str(key): int(item) for key, item in value.items()})


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


__all__ = ["ProgressStatus", "TaskProgressProjection", "build_task_progress_projection"]
