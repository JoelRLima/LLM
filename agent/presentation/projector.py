"""Deterministic projection of redacted trace envelopes into activity rows."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from agent.observability.envelopes import ObservationEnvelope, ObservationSource
from agent.presentation.models import Activity
from agent.runtime.event_kinds import RuntimeEventKind

_EVENT_TITLES = {
    kind.value: kind.value.replace("_", " ").title() for kind in RuntimeEventKind
}
_EVENT_CATEGORIES: dict[str, str] = {
    "task_node_started": "task",
    "task_outcome": "final",
    "task_blocked": "warning/error",
    "direct_response": "task",
    "plan_created": "plan",
    "plan_extended": "plan",
    "replan": "recovery",
    "replan_blocked": "recovery",
    "reasoning_boundary_plan_proposed": "plan",
    "step_blocked": "step",
    "step_cancelled": "step",
    "step_completed": "step",
    "step_failed": "step",
    "step_skipped": "step",
    "step_unverified": "step",
    "model_call_started": "model",
    "model_call_completed": "model",
    "tool_discovery": "tool",
    "tool_start": "tool",
    "tool_end": "tool",
    "tool_denied": "approval/policy",
    "approval_requested": "approval/policy",
    "approval_approved": "approval/policy",
    "task_policy_decision": "approval/policy",
    "validation_repair": "validation",
    "checkpoint_deferred": "checkpoint",
    "checkpoint_persistence_failed": "checkpoint",
    "hierarchical_started": "task",
    "hierarchical_fallback": "recovery",
    "hierarchical_completed": "task",
    "warning": "warning/error",
    "error": "warning/error",
    "hard_block": "warning/error",
    "cost_limit": "warning/error",
    "watchdog": "observer/diagnostic",
    "route_transition": "task",
    "canonical_review_amendment": "validation",
    "canonical_review_rejected": "validation",
    "canonical_review_required_rejection": "validation",
    "cache_hit": "metric",
    "code_analysis_started": "task",
    "code_analysis_completed": "task",
    "code_context_selected": "task",
    "continuation_plan_proposed": "plan",
    "effect_waiver_bound": "approval/policy",
    "legacy_started": "task",
}
_TERMINAL_KINDS = frozenset(
    {
        "final",
        "task_outcome",
        "task_blocked",
        "step_completed",
        "step_failed",
        "step_skipped",
        "step_cancelled",
        "step_unverified",
        "hard_block",
        "cost_limit",
        "error",
        "canonical_review_rejected",
        "canonical_review_required_rejection",
    }
)
_ACTIVE_KINDS = frozenset(
    {
        "task_node_started",
        "hierarchical_started",
        "model_call_started",
        "tool_start",
        "code_analysis_started",
    }
)


def _text(value: Any, fallback: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback


def _payload_fields(envelope: ObservationEnvelope) -> tuple[str, Mapping[str, Any], str | None, str | None]:
    payload = envelope.payload
    if envelope.source is ObservationSource.RUNTIME_EVENT:
        kind = _text(payload.get("type"), "unknown_event")
        data = payload.get("data")
        data_map = data if isinstance(data, Mapping) else {}
        return kind, data_map, None, None
    if envelope.source is ObservationSource.DIAGNOSTIC:
        kind = _text(payload.get("kind"), "diagnostic")
        data = payload.get("data")
        data_map = data if isinstance(data, Mapping) else {}
        return kind, data_map, _text(payload.get("severity"), "info"), _text(payload.get("status"), "diagnostic")
    return "gap", {}, "warning", "gap"


def _field(payload: Mapping[str, Any], name: str) -> str | None:
    value = payload.get(name)
    return value if isinstance(value, str) and value else None


def _summary(kind: str, data: Mapping[str, Any], payload: Mapping[str, Any], source: ObservationSource) -> str:
    for key in ("summary", "message", "status", "reason", "name", "tool", "model", "provider"):
        value = data.get(key, payload.get(key))
        if isinstance(value, str) and value.strip():
            return value.strip()
    if source is ObservationSource.GAP:
        start, end = payload.get("start_sequence"), payload.get("end_sequence")
        if isinstance(start, int) and isinstance(end, int):
            return f"Observations unavailable: {start}-{end}"
    return kind.replace("_", " ")


def project_activity(envelope: ObservationEnvelope, *, bookmarked: bool = False) -> Activity:
    """Project one already-redacted envelope without model calls or I/O."""

    kind, data, severity, status = _payload_fields(envelope)
    payload = envelope.payload
    if envelope.source is ObservationSource.RUNTIME_EVENT:
        category = _EVENT_CATEGORIES.get(kind, "task")
        title = _EVENT_TITLES.get(kind, "Unknown Runtime Event")
        status = status or _field(data, "status") or _field(payload, "status")
        severity = severity or _field(data, "severity") or ("error" if kind in {"error", "hard_block"} else None)
    elif envelope.source is ObservationSource.DIAGNOSTIC:
        category = "observer/diagnostic"
        title = f"Diagnostic: {kind.replace('_', ' ').title()}"
    else:
        category = "observer/diagnostic"
        title = "Completeness Gap"
        status = "gap"
    ids = {name: _field(payload, name) or _field(data, name) for name in (
        "root_task_id", "task_id", "parent_task_id", "node_id", "plan_id", "step_id", "invocation_id"
    )}
    step_value = payload.get("step", data.get("step"))
    step_text = str(step_value) if isinstance(step_value, int) and not isinstance(step_value, bool) else None
    summary = _summary(kind, data, payload, envelope.source)
    detail = dict(data)
    if envelope.source is ObservationSource.GAP:
        detail = dict(payload)
    return Activity(
        sequence=envelope.sequence,
        timestamp=envelope.timestamp,
        source=envelope.source.value,
        category=category,
        kind=kind,
        severity=severity,
        status=status,
        title=title,
        summary=summary,
        run_id=envelope.run_id,
        root_task_id=ids["root_task_id"],
        task_id=ids["task_id"],
        parent_task_id=ids["parent_task_id"],
        node_id=ids["node_id"],
        plan_id=ids["plan_id"],
        step_id=ids["step_id"] or step_text,
        invocation_id=ids["invocation_id"],
        detail_available=True,
        active=kind in _ACTIVE_KINDS,
        terminal=kind in _TERMINAL_KINDS or envelope.source is ObservationSource.GAP,
        gap=envelope.source is ObservationSource.GAP,
        bookmarked=bookmarked,
        data=detail,
    )


class ActivityProjection:
    """Compatibility-friendly stateless projector facade."""

    @staticmethod
    def project(envelope: ObservationEnvelope, *, bookmarked: bool = False) -> Activity:
        return project_activity(envelope, bookmarked=bookmarked)

    @staticmethod
    def project_many(envelopes: Iterable[ObservationEnvelope], *, bookmarks: Iterable[int] = ()) -> tuple[Activity, ...]:
        selected = frozenset(bookmarks)
        return tuple(project_activity(item, bookmarked=item.sequence in selected) for item in envelopes)


def project_activities(envelopes: Iterable[ObservationEnvelope], *, bookmarks: Iterable[int] = ()) -> tuple[Activity, ...]:
    return ActivityProjection.project_many(envelopes, bookmarks=bookmarks)


__all__ = ["ActivityProjection", "project_activities", "project_activity"]
