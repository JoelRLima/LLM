"""Bounded event projections used by task reports."""

from __future__ import annotations

from typing import Any, Dict, List

from agent.reporting.public_safety import sanitize_public_text


def project_planner_outcome(events: List[Dict[str, Any]]) -> str | None:
    outcome = None
    for event in events or []:
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")
        if event_type == "direct_response":
            outcome = "direct_response"
        elif event_type == "plan_created":
            outcome = "use_tools"
        elif event_type == "hard_block":
            outcome = "blocked"
    return outcome


def project_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Project the existing event taxonomy into bounded post-mortem evidence."""

    projected: List[Dict[str, Any]] = []
    for event in events or []:
        summary = project_event(event)
        if summary is not None:
            projected.append(summary)
        if len(projected) >= 50:
            break
    return projected


def project_event(event: Any) -> Dict[str, Any] | None:
    if not isinstance(event, dict):
        return None
    event_type = str(event.get("type") or "")
    raw_data = event.get("data")
    data: Dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
    if event_type in {"direct_response", "hard_block", "plan_created"}:
        summary: Dict[str, Any] = {"type": event_type}
        if event_type == "plan_created":
            summary["steps"] = int(data.get("steps") or 0)
        elif event_type == "hard_block":
            summary["reason_code"] = data.get("reason_code") or "PLAN_BLOCKED"
        return summary
    if event_type == "route_transition":
        return {
            "type": event_type,
            "route": data.get("route"),
            "disposition": data.get("disposition"),
            "reason_code": data.get("reason_code"),
            "next_route": data.get("next_route"),
            "action": data.get("action"),
        }
    if event_type not in {"tool_start", "tool_end", "tool_denied"}:
        return None
    summary = {"type": event_type, "invocation_id": data.get("invocation_id")}
    if event_type != "tool_start":
        summary["status"] = data.get("status")
    if event_type == "tool_denied":
        summary["reason_code"] = data.get("reason_code") or data.get("reason")
    return summary


def extract_replan_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    replans = []
    for event in events or []:
        if not isinstance(event, dict) or event.get("type") != "replan":
            continue
        data = event.get("data") or {}
        replans.append({
            "original_step": data.get("original_step"),
            "error": sanitize_public_text(data.get("error", "")),
            "strategy": data.get("strategy", ""),
            "replacement_steps": data.get("replacement_steps", 0),
        })
    return replans


__all__ = [
    "extract_replan_events",
    "project_event",
    "project_events",
    "project_planner_outcome",
]
