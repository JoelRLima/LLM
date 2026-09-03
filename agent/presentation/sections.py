"""Deterministic summaries derived only from persisted runtime facts."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from agent.observability.envelopes import ObservationEnvelope, ObservationSource
from agent.presentation.models import unavailable_section

MAX_SECTION_RECORDS = 256
MAX_SECTION_ITEMS = 32
MAX_SECTION_DEPTH = 4

_PLAN_KINDS = frozenset(
    {
        "plan_created",
        "plan_extended",
        "reasoning_boundary_plan_proposed",
        "continuation_plan_proposed",
    }
)
_STEP_KINDS = frozenset(
    {
        "step_blocked",
        "step_cancelled",
        "step_completed",
        "step_failed",
        "step_skipped",
        "step_unverified",
    }
)
_MODEL_KINDS = frozenset({"model_call_started", "model_call_completed"})
_TOOL_KINDS = frozenset({"tool_discovery", "tool_start", "tool_end", "tool_denied", "cache_hit"})
_VALIDATION_KINDS = frozenset(
    {
        "validation_repair",
        "canonical_review_amendment",
        "canonical_review_rejected",
        "canonical_review_required_rejection",
        "step_unverified",
    }
)
_RECOVERY_KINDS = frozenset(
    {
        "replan",
        "replan_blocked",
        "hierarchical_fallback",
        "step_cancelled",
        "task_blocked",
        "task_resumed",
        "deferred_condition_blocked",
        "cost_limit",
    }
)
_SUMMARY_FIELDS = (
    "operation",
    "provider",
    "call_number",
    "success",
    "status",
    "ok",
    "tool",
    "invocation_id",
    "lifecycle",
    "step",
    "step_id",
    "plan_id",
    "reason",
    "strategy",
    "field",
    "replacement_steps",
    "duration_ms",
    "token_count",
    "estimated_tokens",
)
_METRIC_FIELDS = (
    "metrics",
    "metric",
    "usage",
    "duration_ms",
    "token_count",
    "estimated_tokens",
    "reserved_tokens",
)
_CHANGE_FIELDS = ("changes", "changed_files", "change_count", "changed_file_count", "files_changed")


def _copy_value(value: Any, depth: int = 0) -> Any:
    if depth > MAX_SECTION_DEPTH:
        return "<section-data-depth-truncated>"
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _copy_value(item, depth + 1)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))[:MAX_SECTION_ITEMS]
        }
    if isinstance(value, (list, tuple)):
        return [_copy_value(item, depth + 1) for item in list(value)[:MAX_SECTION_ITEMS]]
    return None


def _runtime_parts(envelope: ObservationEnvelope) -> tuple[str, Mapping[str, Any], Mapping[str, Any]] | None:
    if envelope.source is not ObservationSource.RUNTIME_EVENT:
        return None
    payload = envelope.payload
    kind = payload.get("type")
    if not isinstance(kind, str) or not kind:
        return None
    data = payload.get("data")
    return kind, payload, data if isinstance(data, Mapping) else {}


def _field(payload: Mapping[str, Any], data: Mapping[str, Any], name: str) -> Any:
    value = payload.get(name)
    return value if value is not None else data.get(name)


def _event_record(
    sequence: int,
    kind: str,
    payload: Mapping[str, Any],
    data: Mapping[str, Any],
    *,
    fields: Iterable[str] = _SUMMARY_FIELDS,
) -> dict[str, Any]:
    record: dict[str, Any] = {"sequence": sequence, "kind": kind}
    for name in fields:
        value = _field(payload, data, name)
        if value is not None:
            record[name] = _copy_value(value)
    return record


def _available(records: list[dict[str, Any]], key: str = "events") -> Mapping[str, Any]:
    return {"status": "available", key: records[:MAX_SECTION_RECORDS], "count": len(records)}


def _append_bounded(records: list[dict[str, Any]], value: dict[str, Any]) -> None:
    if len(records) < MAX_SECTION_RECORDS:
        records.append(value)


def _plan_section(
    plans: list[dict[str, Any]],
    steps: list[dict[str, Any]],
) -> Mapping[str, Any]:
    if not plans and not steps:
        return unavailable_section("no persisted plan or step facts")
    result: dict[str, Any] = {"status": "available", "plans": plans[:MAX_SECTION_RECORDS]}
    if steps:
        result["steps"] = steps[:MAX_SECTION_RECORDS]
    elif plans:
        result["steps"] = plans[-1].get("plan", [])
    result["plan_count"] = len(plans)
    result["step_event_count"] = len(steps)
    return result


def _collect_plan(
    kind: str,
    payload: Mapping[str, Any],
    data: Mapping[str, Any],
    sequence: int,
    plans: list[dict[str, Any]],
    steps: list[dict[str, Any]],
) -> None:
    if kind in _PLAN_KINDS:
        plan_value = data.get("plan")
        plan = _copy_value(plan_value) if isinstance(plan_value, (Mapping, list, tuple)) else []
        if isinstance(plan, Mapping):
            plan = [plan]
        entry: dict[str, Any] = {"sequence": sequence, "kind": kind, "plan": plan}
        step_count = data.get("steps")
        if isinstance(step_count, int) and not isinstance(step_count, bool) and step_count >= 0:
            entry["step_count"] = step_count
        _append_bounded(plans, entry)
    elif kind in _STEP_KINDS:
        _append_bounded(steps, _event_record(sequence, kind, payload, data))


def _collect_event_groups(
    kind: str,
    payload: Mapping[str, Any],
    data: Mapping[str, Any],
    sequence: int,
    buckets: dict[str, list[dict[str, Any]]],
) -> None:
    for name, kinds in (
        ("model_calls", _MODEL_KINDS),
        ("tools", _TOOL_KINDS),
        ("validation", _VALIDATION_KINDS),
        ("recovery", _RECOVERY_KINDS),
    ):
        if kind in kinds:
            _append_bounded(buckets[name], _event_record(sequence, kind, payload, data))


def _collect_explicit_facts(
    kind: str,
    data: Mapping[str, Any],
    sequence: int,
    buckets: dict[str, list[dict[str, Any]]],
) -> None:
    changes = {name: _copy_value(data[name]) for name in _CHANGE_FIELDS if name in data}
    if changes:
        _append_bounded(buckets["changes"], {"sequence": sequence, "kind": kind, **changes})
    metrics = {name: _copy_value(data[name]) for name in _METRIC_FIELDS if name in data}
    if metrics:
        _append_bounded(buckets["metrics"], {"sequence": sequence, "kind": kind, **metrics})


def merge_sections(
    derived: Mapping[str, Mapping[str, Any]],
    reader: Any,
) -> dict[str, Mapping[str, Any]]:
    """Prefer an explicit canonical reader section, retaining persisted fallbacks."""

    if reader is None:
        return dict(derived)
    try:
        value = reader()
    except Exception:
        return dict(derived)
    if not isinstance(value, Mapping):
        return dict(derived)
    result: dict[str, Mapping[str, Any]] = {}
    for name, fallback in derived.items():
        selected = value.get(name)
        result[name] = (
            selected
            if isinstance(selected, Mapping) and selected.get("status") != "unavailable"
            else fallback
        )
    return result


def derive_sections(records: Iterable[ObservationEnvelope]) -> dict[str, Mapping[str, Any]]:
    """Project available presentation sections from canonical event payloads."""

    buckets: dict[str, list[dict[str, Any]]] = {
        "plans": [],
        "steps": [],
        "model_calls": [],
        "tools": [],
        "validation": [],
        "recovery": [],
        "changes": [],
        "metrics": [],
    }

    for envelope in records:
        parts = _runtime_parts(envelope)
        if parts is None:
            continue
        kind, payload, data = parts
        _collect_plan(kind, payload, data, envelope.sequence, buckets["plans"], buckets["steps"])
        _collect_event_groups(kind, payload, data, envelope.sequence, buckets)
        _collect_explicit_facts(kind, data, envelope.sequence, buckets)

    return {
        "plan_steps": _plan_section(buckets["plans"], buckets["steps"]),
        "model_calls": _available(buckets["model_calls"], "calls")
        if buckets["model_calls"]
        else unavailable_section("no persisted model-call facts"),
        "tools": _available(buckets["tools"]) if buckets["tools"] else unavailable_section("no persisted tool facts"),
        "validation": _available(buckets["validation"])
        if buckets["validation"]
        else unavailable_section("no persisted validation facts"),
        "recovery": _available(buckets["recovery"])
        if buckets["recovery"]
        else unavailable_section("no persisted recovery or cancellation facts"),
        "changes": _available(buckets["changes"])
        if buckets["changes"]
        else unavailable_section("no persisted canonical change facts"),
        "metrics": _available(buckets["metrics"])
        if buckets["metrics"]
        else unavailable_section("no persisted canonical metric facts"),
    }


__all__ = ["derive_sections", "merge_sections"]
