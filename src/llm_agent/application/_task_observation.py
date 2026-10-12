"""Private primitive-only settlement and inspection projections."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, cast

from llm_agent.agent.observability.trace_store import TraceCorruptError as _TraceCorruptError
from llm_agent.agent.observability.trace_store import TraceUnavailableError as _TraceUnavailableError
from llm_agent.application.inspection.errors import InspectionCorruptDataError, InspectionUnavailableError


def _primitive(value: Any) -> Any:
    """Copy only primitives; exact scalar types reject str/int Agent enums."""
    if value is None or type(value) in (bool, int, float, str):
        return value
    if any(base.__module__.startswith("llm_agent.agent") for base in type(value).__mro__):
        raise TypeError("public observation contains an Agent owner")
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise TypeError("public observation keys must be strings")
        return {key: _primitive(item) for key, item in value.items()}
    if type(value) is list:
        return [_primitive(item) for item in value]
    if type(value) is tuple:
        return tuple(_primitive(item) for item in value)
    raise TypeError("public observation contains a non-primitive value")


def _receipt(value: Any) -> dict[str, Any]:
    receipt = getattr(value, "receipt", None)
    if isinstance(receipt, Mapping) and receipt:
        return cast(dict[str, Any], _primitive(receipt))
    snapshot = getattr(value, "canonical_snapshot", None) or getattr(value, "snapshot", None)
    if snapshot is None:
        return {}
    data = snapshot.to_dict()
    facts = data.get("projection_facts") or {}
    outcome = data.get("operational_outcome") or {}
    result = {key: facts[key] for key in ("tools", "validation", "rollback") if key in facts}
    if "files_affected" in outcome:
        result["files_affected"] = outcome["files_affected"]
    if "validation_status" in outcome and "validation" not in result:
        status = outcome["validation_status"]
        result["validation"] = {"ran": status is not None, "outcome": status}
    count = facts.get("replan_count")
    if type(count) is int:
        result["replan"] = {"occurred": count > 0, "count": count}
    if data.get("status") is not None:
        result["status"] = data["status"]
    if data.get("failure_fact"):
        result["error"] = data["failure_fact"]
    if getattr(value, "report_path", None):
        result["report_path"] = value.report_path
    return cast(dict[str, Any], _primitive(result))


def _observe_task_settlement(
    settlement: Any, *, channel: Literal["headless", "interactive"],
) -> dict[str, Any]:
    value = settlement._value
    if channel == "headless":
        return cast(dict[str, Any], _primitive(value.to_dict()))
    if channel != "interactive":
        raise ValueError("unsupported settlement channel")
    result: dict[str, Any] = {}
    for name in ("answer", "error", "status", "success", "summary", "reason_code", "interaction_usage"):
        item = getattr(value, name, None)
        if item is not None:
            result[name] = item
    resolution = getattr(value, "resolution", None)
    if resolution is not None:
        result["resolution"] = {key: getattr(getattr(resolution, key, None), "value", getattr(resolution, key, None))
                                for key in ("action", "boundary", "provenance", "ambiguity", "directive",
                                            "deliberation_profile", "reason_code")}
    run_result = getattr(value, "run_result", None)
    if run_result is not None:
        result["receipt"] = _receipt(run_result)
        result["report_path"] = getattr(run_result, "report_path", None)
    result["legacy_transcript"] = settlement._legacy
    return cast(dict[str, Any], _primitive(result))


def _read_runtime_inspection(owner: Any) -> dict[str, Any]:
    """Only the fields already rendered by interactive /inspect; no query/reader API."""
    try:
        snapshot = owner.inspection_service().snapshot(limit=100)
        data = snapshot.to_dict()
    except _TraceCorruptError as exc:
        raise InspectionCorruptDataError(str(exc)) from exc
    except _TraceUnavailableError as exc:
        raise InspectionUnavailableError(str(exc)) from exc
    fields = ("run", "heartbeat", "timeline", "current", "plan_steps", "model_calls", "tools",
              "validation", "recovery", "changes", "metrics", "convergence", "warnings", "selected_detail", "issues")
    observation = {key: data[key] for key in fields}
    observation["run"] = {key: data["run"][key] for key in ("run_id", "status", "completeness", "liveness", "mode")}
    observation["timeline"] = [{key: item[key] for key in ("sequence", "timestamp", "source", "category", "title", "status")} for item in data["timeline"]]
    observation["warnings"] = [{key: item[key] for key in ("sequence", "title", "summary")} for item in data["warnings"]]
    observation["heartbeat"] = {key: data["heartbeat"].get(key) for key in ("observer_heartbeat", "semantic_activity", "silence")}
    return cast(dict[str, Any], _primitive(observation))
