"""Bounded, immutable inspection-query contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from agent.observability.envelopes import ObservationSource
from agent.observability.redaction import canonical_json, redact_text
from agent.observability.silence import parse_timestamp

MAX_QUERY_LIMIT = 500
MAX_SEARCH_CHARS = 256


def _match_time(value: str, start: str | None, end: str | None) -> bool:
    if start is None and end is None:
        return True
    try:
        selected = parse_timestamp(value)
        start_value = parse_timestamp(start) if start is not None else None
        end_value = parse_timestamp(end) if end is not None else None
    except (TypeError, ValueError, OverflowError):
        return False
    return (start_value is None or selected >= start_value) and (end_value is None or selected <= end_value)


def _matches_sequence(activity: Any, selected: "InspectionQuery", after_sequence: int) -> bool:
    return (
        activity.sequence > after_sequence
        and (selected.sequence_start is None or activity.sequence >= selected.sequence_start)
        and (selected.sequence_end is None or activity.sequence <= selected.sequence_end)
    )


def _matches_classification(activity: Any, selected: "InspectionQuery") -> bool:
    if selected.sources:
        source = activity.source.casefold()
        semantic_alias = activity.source == ObservationSource.RUNTIME_EVENT.value and "semantic" in selected.sources
        if source not in selected.sources and not semantic_alias:
            return False
    return all(
        (
            not selected.event_kinds or activity.kind.casefold() in selected.event_kinds,
            not selected.activity_categories or activity.category.casefold() in selected.activity_categories,
            not selected.severities or (activity.severity or "").casefold() in selected.severities,
            not selected.statuses or (activity.status or "").casefold() in selected.statuses,
        )
    )


def _matches_identity(activity: Any, selected: "InspectionQuery") -> bool:
    if selected.task_id and activity.task_id != selected.task_id:
        return False
    if selected.root_task_id and activity.root_task_id != selected.root_task_id:
        return False
    if selected.step is not None and activity.step_id != str(selected.step):
        return False
    if selected.invocation_id and activity.invocation_id != selected.invocation_id:
        return False
    if not selected.correlation_id:
        return True
    identity_values = {
        activity.run_id,
        activity.task_id,
        activity.parent_task_id,
        activity.node_id,
        activity.plan_id,
        activity.step_id,
        activity.invocation_id,
    }
    data_correlation = activity.data.get("correlation_id")
    if isinstance(data_correlation, str):
        identity_values.add(data_correlation)
    return selected.correlation_id in identity_values


def matches_activity(activity: Any, selected: "InspectionQuery", after_sequence: int) -> bool:
    return all(
        (
            _matches_sequence(activity, selected, after_sequence),
            _matches_classification(activity, selected),
            _matches_identity(activity, selected),
            _match_time(activity.timestamp, selected.time_start, selected.time_end),
            not selected.bookmarked_only or activity.bookmarked,
            not selected.search or selected.search.casefold() in canonical_json(activity.to_dict()).casefold(),
        )
    )


def _bounded_int(value: Any, name: str, *, minimum: int = 0, maximum: int = 2**31 - 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < minimum or value > maximum:
        raise ValueError(f"{name} is outside its bounded range")
    return value


def _optional_text(value: Any, name: str, *, limit: int = 256) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string or null")
    return redact_text(value, limit=limit)


def _validated_timestamp(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a valid ISO timestamp")
    try:
        parse_timestamp(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a valid ISO timestamp") from exc
    return value


def _optional_timestamp(value: Any, name: str) -> str | None:
    selected = _optional_text(value, name, limit=128)
    return None if selected is None else _validated_timestamp(selected, name)


def _string_set(values: Iterable[str] | None, name: str) -> frozenset[str]:
    if values is None:
        return frozenset()
    selected: set[str] = set()
    for item in values:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{name} contains an invalid value")
        selected.add(redact_text(item.strip().casefold(), limit=96))
    if len(selected) > 64:
        raise ValueError(f"{name} is too large")
    return frozenset(selected)


@dataclass(frozen=True, slots=True)
class InspectionQuery:
    """Bounded read-only timeline filters."""

    sequence_start: int | None = None
    sequence_end: int | None = None
    sources: frozenset[str] = frozenset()
    event_kinds: frozenset[str] = frozenset()
    activity_categories: frozenset[str] = frozenset()
    severities: frozenset[str] = frozenset()
    statuses: frozenset[str] = frozenset()
    task_id: str | None = None
    root_task_id: str | None = None
    step: int | None = None
    correlation_id: str | None = None
    invocation_id: str | None = None
    time_start: str | None = None
    time_end: str | None = None
    search: str | None = None
    bookmarked_only: bool = False

    def __post_init__(self) -> None:
        if self.sequence_start is not None:
            _bounded_int(self.sequence_start, "sequence_start", minimum=1)
        if self.sequence_end is not None:
            _bounded_int(self.sequence_end, "sequence_end", minimum=1)
        if self.sequence_start is not None and self.sequence_end is not None and self.sequence_end < self.sequence_start:
            raise ValueError("sequence_end must not precede sequence_start")
        for name in ("sources", "event_kinds", "activity_categories", "severities", "statuses"):
            value = getattr(self, name)
            if not isinstance(value, frozenset) or len(value) > 64:
                raise ValueError(f"{name} is invalid or too large")
        if self.step is not None:
            _bounded_int(self.step, "step", minimum=0)
        if not isinstance(self.bookmarked_only, bool):
            raise TypeError("bookmarked_only must be a boolean")
        start = _validated_timestamp(self.time_start, "time_start") if self.time_start is not None else None
        end = _validated_timestamp(self.time_end, "time_end") if self.time_end is not None else None
        if start is not None and end is not None and parse_timestamp(end) < parse_timestamp(start):
            raise ValueError("time_end must not precede time_start")

    @classmethod
    def build(
        cls,
        *,
        sequence_start: int | None = None,
        sequence_end: int | None = None,
        sources: Iterable[str] | None = None,
        event_kinds: Iterable[str] | None = None,
        activity_categories: Iterable[str] | None = None,
        severities: Iterable[str] | None = None,
        statuses: Iterable[str] | None = None,
        task_id: str | None = None,
        root_task_id: str | None = None,
        step: int | None = None,
        correlation_id: str | None = None,
        invocation_id: str | None = None,
        time_start: str | None = None,
        time_end: str | None = None,
        search: str | None = None,
        bookmarked_only: bool = False,
    ) -> "InspectionQuery":
        return cls(
            sequence_start=sequence_start,
            sequence_end=sequence_end,
            sources=_string_set(sources, "sources"),
            event_kinds=_string_set(event_kinds, "event_kinds"),
            activity_categories=_string_set(activity_categories, "activity_categories"),
            severities=_string_set(severities, "severities"),
            statuses=_string_set(statuses, "statuses"),
            task_id=_optional_text(task_id, "task_id"),
            root_task_id=_optional_text(root_task_id, "root_task_id"),
            step=step,
            correlation_id=_optional_text(correlation_id, "correlation_id"),
            invocation_id=_optional_text(invocation_id, "invocation_id"),
            time_start=_optional_timestamp(time_start, "time_start"),
            time_end=_optional_timestamp(time_end, "time_end"),
            search=_optional_text(search, "search", limit=MAX_SEARCH_CHARS),
            bookmarked_only=bookmarked_only,
        )

    @property
    def is_bounded(self) -> bool:
        return self.sequence_end is not None or self.time_end is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence_start": self.sequence_start,
            "sequence_end": self.sequence_end,
            "sources": sorted(self.sources),
            "event_kinds": sorted(self.event_kinds),
            "activity_categories": sorted(self.activity_categories),
            "severities": sorted(self.severities),
            "statuses": sorted(self.statuses),
            "task_id": self.task_id,
            "root_task_id": self.root_task_id,
            "step": self.step,
            "correlation_id": self.correlation_id,
            "invocation_id": self.invocation_id,
            "time_start": self.time_start,
            "time_end": self.time_end,
            "search": self.search,
            "bookmarked_only": self.bookmarked_only,
        }


__all__ = ["InspectionQuery", "MAX_QUERY_LIMIT", "MAX_SEARCH_CHARS", "matches_activity"]
