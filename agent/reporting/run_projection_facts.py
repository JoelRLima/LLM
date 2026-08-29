"""Bounded immutable facts consumed after canonical snapshot publication."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any

from agent.execution_incidents import MAX_EXECUTION_INCIDENTS
from agent.planning.plan_model import Plan, serialize_plan
from agent.reporting.evaluation_arg_projection import project_evaluation_args
from agent.reporting.observation_evidence import project_tool_observation
from agent.reporting.public_safety import sanitize_public_text
from agent.reporting.run_projection_model import RunProjectionFacts, thaw_projection
from agent.reporting.run_receipt_support import executed_projection, execution_incidents
from agent.reporting.task_report_events import (
    extract_replan_events,
    project_events,
    project_planner_outcome,
)
from agent.runtime.events import bounded_event_data
from agent.runtime.mutation_evidence import project_mutation_evidence

MAX_PROJECTION_HISTORY = 50
MAX_PROJECTION_FILES = 128
MAX_PROJECTION_PATH_CHARS = 512
MAX_PROJECTION_TEXT = 500


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _text(value: Any, limit: int = MAX_PROJECTION_TEXT) -> str:
    if value is None:
        raw = ""
    elif isinstance(value, str):
        raw = value
    else:
        try:
            raw = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            raw = str(value)
    safe = str(sanitize_public_text(raw))
    return safe[:limit] + ("..." if len(safe) > limit else "")


def _project_args(raw: Any) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        return {}
    projected: dict[str, str] = {}
    for key in ("file_path", "path", "target", "mode", "action"):
        value = raw.get(key)
        if isinstance(value, (str, int, float, bool)):
            projected[key] = _text(value, 200)
    return projected


def _project_history_entry(
    index: int,
    entry: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], Mapping[str, Any]]:
    evidence = project_tool_observation(entry)
    artifact = project_mutation_evidence(entry.get("result"))
    tool = evidence.base_record()
    tool.update(
        {
            "tool": evidence.tool,
            "invocation_id": evidence.invocation_id,
            "status": evidence.status,
            "executed": evidence.executed,
            "error_code": evidence.error_code,
        }
    )
    tool["mutation"] = {
        "attempted": artifact.attempted,
        "occurred": artifact.occurred,
        "rollback_occurred": artifact.rollback_occurred,
        "survives": artifact.survives,
        "affected_files": list(artifact.affected_files[:MAX_PROJECTION_FILES]),
        "validation_status": artifact.validation_status,
        "final_state": artifact.final_state,
    }
    for name in (
        "run_id",
        "root_task_id",
        "task_id",
        "parent_task_id",
        "node_id",
        "plan_id",
        "step_id",
    ):
        value = entry.get(name)
        if isinstance(value, str):
            tool[name] = value[:MAX_PROJECTION_PATH_CHARS]
    result = {
        "ok": evidence.ok is True,
        "error": _text(
            entry.get("result", {}).get("error", "")
            if isinstance(entry.get("result"), Mapping)
            else ""
        ),
        "data_summary": _text(evidence.value) if evidence.present else "",
        "status": evidence.status,
        "executed": evidence.executed,
        "reason_code": evidence.error_code,
        "output_chars": len(_text(evidence.value)) if evidence.present else 0,
        "present": evidence.present,
        "complete": evidence.complete,
        "truncated": evidence.truncated,
        "value_type": evidence.value_type,
    }
    step: dict[str, Any] = {
        "index": index,
        "tool": evidence.tool,
        "args": _project_args(entry.get("args")),
        "result": result,
    }
    if evidence.invocation_id:
        step["invocation_id"] = evidence.invocation_id
    raw_result = entry.get("result")
    if isinstance(raw_result, Mapping) and raw_result.get("cache_hit") is not None:
        step["cache_hit"] = bool(raw_result.get("cache_hit"))
    invocation_result: dict[str, Any] = {
        "status": evidence.status,
        "ok": evidence.ok,
        "executed": evidence.executed,
        "error_code": evidence.error_code,
        "data": evidence.value if evidence.present else None,
    }
    invocation = {
        "tool": evidence.tool,
        "args": project_evaluation_args(entry.get("args")),
        "result": invocation_result,
    }
    if evidence.invocation_id:
        invocation["invocation_id"] = evidence.invocation_id
    for name in (
        "run_id",
        "root_task_id",
        "task_id",
        "parent_task_id",
        "node_id",
        "plan_id",
        "step_id",
    ):
        if name in tool:
            invocation[name] = tool[name]
    return tool, step, _freeze(bounded_event_data(invocation))


def _bounded_events(events: Sequence[Any], allowed: frozenset[str]) -> tuple[Mapping[str, Any], ...]:
    projected: list[Mapping[str, Any]] = []
    for event in events:
        if not isinstance(event, Mapping) or event.get("type") not in allowed:
            continue
        projected.append(_freeze(bounded_event_data(event)))
        if len(projected) >= MAX_PROJECTION_HISTORY:
            break
    return tuple(projected)


def _canonical_plan(state: Any) -> Any:
    plan = getattr(state, "plan", None)
    if not isinstance(plan, Plan):
        return ()
    return _freeze(bounded_event_data({"steps": serialize_plan(plan)}).get("steps", ()))


def build_run_projection_facts(state: Any, *, observed_at: str) -> RunProjectionFacts:
    history = tuple(
        item
        for item in (getattr(state, "tool_history", None) or ())[:MAX_PROJECTION_HISTORY]
        if isinstance(item, Mapping)
    )
    projected_pairs = tuple(
        _project_history_entry(index, entry) for index, entry in enumerate(history)
    )
    tools = tuple(_freeze(tool) for tool, _step, _invocation in projected_pairs)
    steps = tuple(_freeze(step) for _tool, step, _invocation in projected_pairs)
    invocation_evidence = tuple(
        invocation for _tool, _step, invocation in projected_pairs
    )
    incidents = tuple(
        _freeze(bounded_event_data(item))
        for item in execution_incidents(state)[:MAX_EXECUTION_INCIDENTS]
    )
    proposed: list[str] = []
    validation: dict[str, Any] = {"ran": False, "outcome": None}
    rollback: dict[str, Any] = {"occurred": False, "outcome": None}
    effects: list[bool] = []
    corrective_action_count = 0
    for entry, tool in zip(history, tools, strict=True):
        mutation = tool.get("mutation", {})
        for path in mutation.get("affected_files", ()):
            bounded_path = str(path)[:MAX_PROJECTION_PATH_CHARS]
            if bounded_path not in proposed and len(proposed) < MAX_PROJECTION_FILES:
                proposed.append(bounded_path)
        if mutation.get("validation_status") is not None:
            validation = {"ran": True, "outcome": mutation.get("validation_status")}
        if mutation.get("rollback_occurred") is True:
            rollback = {
                "occurred": True,
                "outcome": "restored" if mutation.get("final_state") == "restored" else "unknown",
            }
        if isinstance(tool.get("executed"), bool):
            effects.append(tool["executed"])
        args = entry.get("args")
        if isinstance(args, Mapping) and args.get("action") == "repair":
            corrective_action_count += 1
    if any(item.get("rollback_occurred") is True for item in incidents):
        rollback = {"occurred": True, "outcome": "restored"}
    events = tuple(
        item for item in (getattr(state, "events", None) or ()) if isinstance(item, Mapping)
    )
    event_dicts = [dict(item) for item in events]
    replan_count = sum(item.get("type") == "replan" for item in event_dicts)
    last_result = history[-1].get("result") if history else None
    last_data = last_result.get("data") if isinstance(last_result, Mapping) else None
    metadata = last_data.get("metadata") if isinstance(last_data, Mapping) else None
    output = _text(last_data) if last_data is not None else ""
    total_chars = metadata.get("total_chars") if isinstance(metadata, Mapping) else None
    return RunProjectionFacts(
        objective=_text(getattr(state, "objective", None)) or None,
        tools=tools,
        incidents=incidents,
        proposed_files=tuple(proposed),
        validation=_freeze(validation),
        rollback=_freeze(rollback),
        executed=executed_projection(effects, [dict(item) for item in incidents]),
        repair_count=corrective_action_count,
        replan_count=replan_count,
        report_steps=steps,
        invocation_evidence=invocation_evidence,
        planner_outcome=project_planner_outcome(event_dicts),
        event_summary=tuple(_freeze(item) for item in project_events(event_dicts)),
        replan_events=tuple(_freeze(item) for item in extract_replan_events(event_dicts)),
        report_start_time=observed_at,
        report_end_time=observed_at,
        canonical_plan=_canonical_plan(state),
        route_events=_bounded_events(
            events,
            frozenset({
                "hierarchical_started",
                "hierarchical_completed",
                "hierarchical_fallback",
                "continuation_plan_proposed",
                "hard_block",
                "task_outcome",
            }),
        ),
        validation_events=_bounded_events(
            events,
            frozenset({
                "hard_block",
                "plan_created",
                "plan_extended",
                "replan",
                "tool_denied",
                "error",
            }),
        ),
        output_chars=(
            total_chars
            if type(total_chars) is int
            else len(output)
        ),
        output_truncated=(
            bool(metadata.get("truncated")) if isinstance(metadata, Mapping) else False
        ),
    )


__all__ = ["RunProjectionFacts", "build_run_projection_facts", "thaw_projection"]
