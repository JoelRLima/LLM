"""Pure allowlist projection for completed-run inspection output."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from math import isfinite
from typing import Any, cast

from agent.engineering.summary import MAX_ENGINEERING_SUMMARY_ITEMS, normalize_summary
from agent.observability.envelope_types import ObservationSource
from agent.observability.modes import ObservabilityMode
from agent.observability.silence import SilenceLevel
from agent.observability.trace_types import TraceCompleteness
from agent.runtime.event_kinds import RuntimeEventKind

_EVENT_KINDS = frozenset(kind.value for kind in RuntimeEventKind) | {"diagnostic", "gap"}
_SOURCES = frozenset(source.value for source in ObservationSource)
_COMPLETENESS = frozenset(status.value for status in TraceCompleteness)
_MODES = frozenset(mode.value for mode in ObservabilityMode)
_SILENCE_LEVELS = frozenset(level.value for level in SilenceLevel)
_CATEGORIES = frozenset(
    "approval/policy audit checkpoint final metric model observer/diagnostic "
    "plan recovery step task tool validation warning/error".split()
)
_STATUSES = _COMPLETENESS | frozenset(
    {
        "available", "unavailable", "succeeded", "failed", "blocked", "cancelled",
        "unverified", "ok", "error", "warning", "diagnostic", "gap", "running",
        "completed", "denied",
    }
)
_BOOLEAN = "boolean"
_INTEGER = "integer"
_POSITIVE_INTEGER = "positive_integer"
_NUMBER = "number"
_OMIT = object()
_FieldRule = str | frozenset[str]
_FieldRules = tuple[tuple[str, _FieldRule], ...]
_Source = Mapping[str, Any]
_Target = dict[str, Any]
_Budget = list[int]
_FieldProjector = Callable[[object], _Target]
_PendingLists = list[tuple[_Target, str, object, _FieldProjector]]
_EVENT_COUNTS = (
    "call_number", "step", "step_count", "duration_ms", "token_count",
    "estimated_tokens", "reserved_tokens", "change_count", "changed_file_count",
)
_AVAILABLE = frozenset({"available", "unavailable"})


def _rules(rule: _FieldRule, *keys: str) -> _FieldRules:
    return tuple((key, rule) for key in keys)


_RUN_RULES = (
    _rules(_BOOLEAN, "active") + _rules(_COMPLETENESS, "status", "completeness")
    + _rules(_MODES, "mode") + _rules(_INTEGER, "highest_sequence", "semantic_count", "gap_count")
)
_ACTIVITY_RULES = (
    _rules(_POSITIVE_INTEGER, "sequence") + _rules(_SOURCES, "source")
    + _rules(_CATEGORIES, "category") + _rules(_EVENT_KINDS, "kind")
    + _rules(_BOOLEAN, "active", "terminal", "gap")
)
_EVENT_RULES = (
    _rules(_POSITIVE_INTEGER, "sequence") + _rules(_EVENT_KINDS, "kind")
    + _rules(_STATUSES, "status") + _rules(_BOOLEAN, "success", "ok")
    + _rules(_INTEGER, *_EVENT_COUNTS)
)
_PLAN_RULES = (
    _rules(_POSITIVE_INTEGER, "sequence") + _rules(_EVENT_KINDS, "kind")
    + _rules(_INTEGER, "step_count")
)
_SECTION_RULES: _FieldRules = (("status", _AVAILABLE),) + _rules(_INTEGER, "count")
_PLAN_SECTION_RULES = _rules(_AVAILABLE, "status") + _rules(
    _INTEGER, "plan_count", "step_event_count"
)
_HEARTBEAT_RULES = _rules(_SILENCE_LEVELS, "silence") + _rules(
    _NUMBER, "elapsed_seconds", "heartbeat_age_seconds"
)
_SECTION_KEYS = ("tools", "validation", "recovery", "changes", "metrics", "convergence")
_PROJECTION_KEYS = (
    "schema_version", "run", "current", "plan_steps", "timeline", "tools",
    "validation", "recovery", "changes", "metrics", "warnings", "heartbeat",
    "issues", "convergence",
)


def project_inspection(value: Mapping[str, Any], *, limit: int = 64) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("inspection must be an object")
    effective_limit = min(limit, 64)
    budget = [MAX_ENGINEERING_SUMMARY_ITEMS]
    source = {key: value.get(key) for key in _PROJECTION_KEYS}
    projected: _Target = {}
    _add(projected, "schema_version", 1, budget)
    pending_lists: _PendingLists = []
    _project_primary_sections(source, projected, budget, pending_lists)
    _queue_activity_lists(source, projected, pending_lists)
    _project_sections(source, projected, budget, pending_lists)
    _project_heartbeat_and_issues(source, projected, budget)
    _project_pending_lists(pending_lists, budget, effective_limit)
    return cast(_Target, normalize_summary(projected))


def _project_primary_sections(
    source: _Source, target: _Target, budget: _Budget, pending: _PendingLists
) -> None:
    _project_mapped(source, target, "run", budget, _run_fields)
    _project_mapped(source, target, "current", budget, _activity_fields)
    plan_source = source.get("plan_steps")
    if isinstance(plan_source, Mapping):
        section: _Target = {}
        if _add(target, "plan_steps", section, budget):
            _project_plan_section(plan_source, section, budget, pending)


def _queue_activity_lists(source: _Source, target: _Target, pending: _PendingLists) -> None:
    for key in ("timeline", "warnings"):
        items = source.get(key)
        if isinstance(items, (list, tuple)):
            pending.append((target, key, items, _activity_fields))


def _project_sections(
    source: _Source, target: _Target, budget: _Budget, pending: _PendingLists
) -> None:
    for key in _SECTION_KEYS:
        raw_section = source.get(key)
        if not isinstance(raw_section, Mapping):
            continue
        section: _Target = {}
        if _add(target, key, section, budget):
            _project_section(raw_section, section, budget, pending)


def _project_heartbeat_and_issues(source: _Source, target: _Target, budget: _Budget) -> None:
    _project_mapped(source, target, "heartbeat", budget, _heartbeat_fields)
    issues = source.get("issues")
    if isinstance(issues, (list, tuple)):
        _add(target, "issues", min(len(issues), 1_000_000_000), budget)
    elif _nonnegative_int(issues) is not None:
        _add(target, "issues", issues, budget)


def _project_pending_lists(pending: _PendingLists, budget: _Budget, limit: int) -> None:
    for target, key, items, project_row in pending:
        _project_list(target, key, items, project_row, budget, limit)


def _add(target: _Target, key: str, value: Any, budget: _Budget) -> bool:
    if budget[0] <= 0:
        return False
    target[key] = value
    budget[0] -= 1
    return True


def _nonnegative_int(value: Any, *, positive: bool = False) -> int | None:
    minimum = 1 if positive else 0
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= 1_000_000_000:
        return None
    return value


def _safe_number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value <= 1_000_000_000 else None
    if not isinstance(value, float) or not isfinite(value) or not 0 <= value <= 1_000_000_000:
        return None
    return value


def _token(value: Any, choices: frozenset[str]) -> str | None:
    return value if isinstance(value, str) and value in choices else None


def _field_value(value: Any, rule: _FieldRule) -> Any:
    if rule == _BOOLEAN:
        return value if isinstance(value, bool) else _OMIT
    if rule == _INTEGER:
        return value if _nonnegative_int(value) is not None else _OMIT
    if rule == _POSITIVE_INTEGER:
        return value if _nonnegative_int(value, positive=True) is not None else _OMIT
    if rule == _NUMBER:
        number = _safe_number(value)
        return _OMIT if number is None else number
    if not isinstance(rule, frozenset):
        return _OMIT
    token = _token(value, rule)
    return _OMIT if token is None else token


def _selected_fields(source: object, rules: _FieldRules) -> _Target:
    if not isinstance(source, Mapping):
        return {}
    selected: _Target = {}
    for key, rule in rules:
        item = _field_value(source.get(key), rule)
        if item is not _OMIT:
            selected[key] = item
    return selected


def _copy_fields(source: object, target: _Target, budget: _Budget, rules: _FieldRules) -> None:
    for key, value in _selected_fields(source, rules).items():
        _add(target, key, value, budget)


def _project_mapped(
    source: _Source, target: _Target, key: str, budget: _Budget, projector: _FieldProjector
) -> None:
    raw = source.get(key)
    if not isinstance(raw, Mapping):
        return
    section: _Target = {}
    if _add(target, key, section, budget):
        for field, value in projector(raw).items():
            _add(section, field, value, budget)


def _run_fields(source: object) -> _Target:
    return _selected_fields(source, _RUN_RULES)


def _heartbeat_fields(source: object) -> _Target:
    return _selected_fields(source, _HEARTBEAT_RULES)


def _activity_fields(source: object) -> _Target:
    if not isinstance(source, Mapping):
        return {}
    if source.get("status") == "unavailable":
        return {"status": "unavailable"}
    return _selected_fields(source, _ACTIVITY_RULES)


def _event_fields(source: object) -> _Target:
    return _selected_fields(source, _EVENT_RULES)


def _plan_fields(source: object) -> _Target:
    return _selected_fields(source, _PLAN_RULES)


def _project_section(
    source: _Source, target: _Target, budget: _Budget, pending_lists: _PendingLists
) -> None:
    _copy_fields(source, target, budget, _SECTION_RULES)
    events = source.get("events")
    if isinstance(events, (list, tuple)):
        pending_lists.append((target, "events", events, _event_fields))


def _project_plan_section(
    source: _Source, target: _Target, budget: _Budget, pending_lists: _PendingLists
) -> None:
    _copy_fields(source, target, budget, _PLAN_SECTION_RULES)
    plans = source.get("plans")
    if isinstance(plans, (list, tuple)):
        pending_lists.append((target, "plans", plans, _plan_fields))
    steps = source.get("steps")
    if isinstance(steps, (list, tuple)) and _nonnegative_int(source.get("step_event_count"), positive=True):
        pending_lists.append((target, "steps", steps, _event_fields))


def _project_list(
    target: _Target, key: str, source: object, project_row: _FieldProjector, budget: _Budget, limit: int
) -> None:
    if not isinstance(source, (list, tuple)):
        return
    rows: list[_Target] = []
    if not _add(target, key, rows, budget):
        return
    for item in source[:limit]:
        fields = project_row(item)
        cost = 1 + len(fields)
        if not fields or budget[0] < cost:
            break
        budget[0] -= cost
        rows.append(fields)


__all__ = ["project_inspection"]
